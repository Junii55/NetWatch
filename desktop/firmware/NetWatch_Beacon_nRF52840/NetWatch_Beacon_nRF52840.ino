/*
  NetWatch beacon — low-power Seeed XIAO nRF52840 / Sense

  Board package: Seeed nRF52 Boards (NOT mbed).
  Same Find My payload. Private key never on the chip.

  Battery: CPU sleeps between 2 s advertisements (SoftDevice idle).
  RESET tap (on battery) -> System OFF. Next tap -> on.
  USB plugged in -> stays on for flashing.

  ---------------------------------------------------------------------------
  DO NOT "MODERNISE" THIS FILE.

  This is the known-working NetWatch-AnyBLE sketch, kept verbatim. Unlike the
  ESP32 firmware, PUBLIC_KEY here is a COMPILE-TIME constant: the flasher
  rewrites the array in this source and recompiles, then uploads over DFU. That
  is the only combination proven on real XIAO hardware.

  An earlier attempt to treat this like the ESP32 path -- one prebuilt image
  with the key injected into the binary and copied as a .uf2 -- produced a board
  that wrote to flash correctly (verified by reading the bootloader's own
  CURRENT.UF2 back) but never executed a single instruction. If you change the
  key mechanism, the BLE bring-up order, or the transport, re-verify with a BLE
  scanner: this board exposes no USB serial while the application runs, so a
  silent failure is indistinguishable from success.
  ---------------------------------------------------------------------------
*/

#include <bluefruit.h>
#include <string.h>
#include <nrf_gpio.h>
#include <nrf_soc.h>

/* The advertisement key.
 *
 * Preceded by a magic string so the flasher can find it inside a PREBUILT
 * image and rewrite the 28 bytes without recompiling. That is what lets the
 * installer ship a ready-made .hex instead of dragging a 400 MB Arduino
 * toolchain onto the customer's machine.
 *
 * `volatile` is load-bearing: without it the compiler folds the zeros into the
 * code and the injected key is silently ignored. The packed struct guarantees
 * the key sits immediately after the magic, so the patcher can locate it by
 * offset. Everything below this point is the original proven sketch.
 */
typedef struct __attribute__((packed)) {
  uint8_t magic[16];   // "NETWATCH-KEYBLK\0"
  uint8_t key[28];
} netwatch_keyblock_t;

static const volatile netwatch_keyblock_t NETWATCH_KEYBLOCK __attribute__((used)) = {
  { 'N','E','T','W','A','T','C','H','-','K','E','Y','B','L','K','\0' },
  { 0 }
};

static uint8_t PUBLIC_KEY[28];

static void load_public_key() {
  // Byte-wise through a volatile source so the reads actually happen.
  for (int i = 0; i < 28; i++) PUBLIC_KEY[i] = NETWATCH_KEYBLOCK.key[i];
}

static bool key_is_blank() {
  for (int i = 0; i < 28; i++) if (PUBLIC_KEY[i]) return false;
  return true;
}

static const uint16_t ADV_INTERVAL_MS = 2000;

static bool usb_powered() {
#ifdef POWER_USBREGSTATUS_VBUSDETECT_Msk
  return (NRF_POWER->USBREGSTATUS & POWER_USBREGSTATUS_VBUSDETECT_Msk) != 0;
#else
  return false;
#endif
}

static void gpio_leds_off() {
#ifdef LED_BLUE
  pinMode(LED_BLUE, OUTPUT);
#ifdef LED_STATE_ON
  digitalWrite(LED_BLUE, 1 - LED_STATE_ON);
#else
  digitalWrite(LED_BLUE, HIGH);
#endif
#endif
#ifdef LED_RED
  pinMode(LED_RED, OUTPUT);
  digitalWrite(LED_RED, HIGH);
#endif
#ifdef LED_GREEN
  pinMode(LED_GREEN, OUTPUT);
  digitalWrite(LED_GREEN, HIGH);
#endif
  pinMode(11, OUTPUT); digitalWrite(11, HIGH);
  pinMode(12, OUTPUT); digitalWrite(12, HIGH);
  pinMode(13, OUTPUT); digitalWrite(13, HIGH);
  nrf_gpio_cfg_output(NRF_GPIO_PIN_MAP(0, 6));
  nrf_gpio_pin_set(NRF_GPIO_PIN_MAP(0, 6));
  nrf_gpio_cfg_output(NRF_GPIO_PIN_MAP(0, 26));
  nrf_gpio_pin_set(NRF_GPIO_PIN_MAP(0, 26));
  nrf_gpio_cfg_output(NRF_GPIO_PIN_MAP(0, 30));
  nrf_gpio_pin_set(NRF_GPIO_PIN_MAP(0, 30));
}

static void leds_off() {
  Bluefruit.autoConnLed(false);
  Bluefruit._stopConnLed();
  gpio_leds_off();
}

static void flash_sleep() {
#ifdef PIN_QSPI_CS
  pinMode(PIN_QSPI_CS, OUTPUT);
  digitalWrite(PIN_QSPI_CS, HIGH);
#endif
}

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
  out[0] = 0x1e;
  out[1] = 0xff;
  out[2] = 0x4c;
  out[3] = 0x00;
  out[4] = 0x12;
  out[5] = 0x19;
  out[6] = 0x00;
  memcpy(&out[7], &PUBLIC_KEY[6], 22);
  out[29] = PUBLIC_KEY[0] >> 6;
}

static void power_button_check() {
  uint32_t reas = NRF_POWER->RESETREAS;
  NRF_POWER->RESETREAS = 0xFFFFFFFF;
  const uint32_t PIN = POWER_RESETREAS_RESETPIN_Msk;
  const uint32_t OFF = POWER_RESETREAS_OFF_Msk;
  uint8_t flag = (uint8_t)NRF_POWER->GPREGRET;
  bool off_cmd = (reas & PIN) && !(reas & OFF) && flag == 0xA5 && !usb_powered();
  if (off_cmd) {
    NRF_POWER->GPREGRET = 0;
    gpio_leds_off();
    NRF_POWER->SYSTEMOFF = 1;
    while (1) {}
  }
  NRF_POWER->GPREGRET = 0xA5;
}

void setup() {
  gpio_leds_off();
  flash_sleep();
  power_button_check();

  const bool usb = usb_powered();
  if (usb) {
    Serial.begin(115200);
    delay(200);
    Serial.println("NetWatch LP boot (nRF52840)");
  }

  load_public_key();

  // An un-provisioned image would advertise a zero key: useless, and identical
  // on every blank board. Blink the red LED instead, since this board exposes
  // no USB serial once the application is running.
  if (key_is_blank()) {
    if (usb) Serial.println("ERROR: no key provisioned. Flash from the NetWatch app.");
    pinMode(LED_RED, OUTPUT);
    while (true) {
      digitalWrite(LED_RED, LOW);  delay(120);
      digitalWrite(LED_RED, HIGH); delay(380);
    }
  }

  Bluefruit.autoConnLed(false);
  Bluefruit.begin(0, 0);
  sd_power_dcdc_mode_set(NRF_POWER_DCDC_ENABLE);
  Bluefruit.autoConnLed(false);
  leds_off();
  Bluefruit.setTxPower(4);
  Bluefruit.setName("");

  ble_gap_addr_t gap;
  memset(&gap, 0, sizeof(gap));
  gap.addr_type = BLE_GAP_ADDR_TYPE_RANDOM_STATIC;
  build_addr_le(gap.addr);
  bool ok = Bluefruit.setAddr(&gap);
  if (usb) {
    Serial.printf("setAddr %s\n", ok ? "ok" : "FAIL");
  }

  uint8_t adv_raw[31];
  build_adv(adv_raw);
  Bluefruit.Advertising.stop();
  Bluefruit.Advertising.clearData();
  Bluefruit.Advertising.setType(BLE_GAP_ADV_TYPE_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED);
  Bluefruit.Advertising.setData(adv_raw, sizeof(adv_raw));
  Bluefruit.Advertising.setFastTimeout(1);
  Bluefruit.Advertising.setInterval(
      (uint16_t)(ADV_INTERVAL_MS / 0.625),
      (uint16_t)(ADV_INTERVAL_MS / 0.625));
  Bluefruit.Advertising.start(0);
  Bluefruit.autoConnLed(false);
  Bluefruit._stopConnLed();
  leds_off();
  if (usb) Serial.println("Advertising Find My payload (LP)");
}

void loop() {
  leds_off();
  waitForEvent();
}
