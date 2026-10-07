/*
  NetWatch beacon — ESP32 / C3 / C6 / S3
  ======================================

  Advertises an OpenHaystack-style Apple Find My payload so you can locate an
  item YOU OWN. Not a paired AirTag; it will not appear in Find My > Items.
  Only attach these to your own property.

  THIS SKETCH IS BUILT ONCE, BY US, NOT BY THE CUSTOMER
  -----------------------------------------------------
  The 28-byte advertisement key is NOT compiled in per tag. It lives inside a
  marked struct (NETWATCH_KEYBLOCK) that the desktop app rewrites directly in
  the compiled .bin at flash time — see app/netwatch/keyinject.py. That is what
  turns a 5-15 minute arduino-cli compile into a ~10 second flash.

  Consequences you must respect when editing this file:
    * The key block MUST stay `volatile`, or the compiler will fold the zeros
      into the code and the injected key will be ignored.
    * The magic string must appear EXACTLY ONCE in the final binary. Do not
      print it, duplicate it, or build it from parts.

  Build:  see firmware/build_prebuilt.ps1
  Library: NimBLE-Arduino 1.4.x
*/

#include <NimBLEDevice.h>
#include <NimBLEAdvertising.h>
#include <string.h>

#if NETWATCH_LOW_POWER
  #include <WiFi.h>
  #include "esp_wifi.h"
  #include "esp_bt.h"
#endif

extern "C" {
  int ble_hs_id_set_rnd(const uint8_t *addr);
}

/* ─── patched-at-flash-time key block ──────────────────────────────────── */
typedef struct __attribute__((packed)) {
  uint8_t magic[16];   // "NETWATCH-KEYBLK\0"
  uint8_t key[28];     // advertisement (public) key — written by the flasher
} netwatch_keyblock_t;

static const volatile netwatch_keyblock_t NETWATCH_KEYBLOCK __attribute__((used)) = {
  { 'N','E','T','W','A','T','C','H','-','K','E','Y','B','L','K','\0' },
  { 0 }
};

static uint8_t PUBLIC_KEY[28];

static void load_public_key() {
  // Byte-wise copy: the source is volatile so the compiler cannot assume zeros.
  for (int i = 0; i < 28; i++) {
    PUBLIC_KEY[i] = NETWATCH_KEYBLOCK.key[i];
  }
}

static bool key_is_blank() {
  for (int i = 0; i < 28; i++) {
    if (PUBLIC_KEY[i] != 0) return false;
  }
  return true;
}

static const uint16_t ADV_INTERVAL_MS = 2000;

/* ─── Find My payload ──────────────────────────────────────────────────── */
static void build_addr_le(uint8_t addr[6]) {
  addr[0] = PUBLIC_KEY[5];
  addr[1] = PUBLIC_KEY[4];
  addr[2] = PUBLIC_KEY[3];
  addr[3] = PUBLIC_KEY[2];
  addr[4] = PUBLIC_KEY[1];
  addr[5] = PUBLIC_KEY[0] | 0xC0;
}

static void build_adv(uint8_t out[31]) {
  memset(out, 0, 31);
  out[0] = 0x1e;                         // AD length
  out[1] = 0xff;                         // manufacturer specific
  out[2] = 0x4c;                         // Apple, low byte
  out[3] = 0x00;                         // Apple, high byte
  out[4] = 0x12;                         // Find My type
  out[5] = 0x19;                         // following length (25)
  out[6] = 0x00;                         // status
  memcpy(&out[7], &PUBLIC_KEY[6], 22);   // key bytes 6..27
  out[29] = PUBLIC_KEY[0] >> 6;
  out[30] = 0x00;                        // hint
}

void setup() {
#if NETWATCH_LOW_POWER
  setCpuFrequencyMhz(80);
  WiFi.persistent(false);
  WiFi.mode(WIFI_OFF);
  WiFi.disconnect(true, true);
  esp_wifi_stop();
  esp_wifi_deinit();
#endif

  Serial.begin(115200);
  delay(300);
  Serial.println("NetWatch beacon boot");

  load_public_key();

  if (key_is_blank()) {
    // An un-provisioned image. Advertising a zero key would be useless and
    // would collide with every other blank board, so refuse.
    Serial.println("ERROR: no key provisioned. Flash this board from the NetWatch app.");
    while (true) { delay(5000); }
  }

  const uint8_t printed[6] = {
    (uint8_t)(PUBLIC_KEY[0] | 0xC0),
    PUBLIC_KEY[1], PUBLIC_KEY[2], PUBLIC_KEY[3], PUBLIC_KEY[4], PUBLIC_KEY[5]
  };
  Serial.printf("want printed %02X:%02X:%02X:%02X:%02X:%02X\n",
                printed[0], printed[1], printed[2], printed[3], printed[4], printed[5]);

  uint8_t rnd[6];
  build_addr_le(rnd);

  NimBLEDevice::init("");
  delay(150);
  NimBLEDevice::setOwnAddrType(BLE_OWN_ADDR_RANDOM);
  NimBLEDevice::setOwnAddr(rnd);
  int rc = ble_hs_id_set_rnd(rnd);
  Serial.printf("set_rnd rc=%d\n", rc);   // 0 = good

#if NETWATCH_LOW_POWER
  #ifdef ESP_PWR_LVL_P6
    NimBLEDevice::setPower(ESP_PWR_LVL_P6);
  #elif defined(ESP_PWR_LVL_P3)
    NimBLEDevice::setPower(ESP_PWR_LVL_P3);
  #endif
  // esp_bt_sleep_enable() belongs to the CLASSIC Bluetooth controller API and
  // only exists on parts that have one. The C6/H2 (BLE-only) do not declare it,
  // so calling it unguarded breaks the build for those targets.
  #if defined(CONFIG_IDF_TARGET_ESP32) || defined(CONFIG_IDF_TARGET_ESP32C3)
    esp_bt_sleep_enable();
  #endif
#endif

  uint8_t adv_raw[31];
  build_adv(adv_raw);

  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  NimBLEAdvertisementData data;
  data.addData(adv_raw, sizeof(adv_raw));
  adv->setAdvertisementData(data);
  adv->setMinInterval((uint16_t)(ADV_INTERVAL_MS / 0.625));
  adv->setMaxInterval((uint16_t)(ADV_INTERVAL_MS / 0.625));
  adv->start();

  Serial.println("Advertising Find My payload");
#if NETWATCH_LOW_POWER
  Serial.flush();
  Serial.end();
#endif
}

void loop() {
#if NETWATCH_LOW_POWER
  vTaskDelay(portMAX_DELAY);
#else
  delay(1000);
#endif
}
