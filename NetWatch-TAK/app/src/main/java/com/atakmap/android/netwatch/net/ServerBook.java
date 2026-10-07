
package com.atakmap.android.netwatch.net;

import android.content.Context;
import android.content.SharedPreferences;

import com.atakmap.coremap.log.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * The operator's saved NetWatch servers, and which one is in use.
 *
 * <p>Replaces the hardcoded address list the previous plugin shipped. Someone who
 * runs NetWatch on a desktop at home and a laptop in a vehicle keeps both here
 * and switches between them.
 *
 * <p>Pair codes live in these preferences in clear. That is worth being honest
 * about: a code grants read access to tag locations, so it is a credential, and
 * the protection it has is Android's per-app storage — the same place ATAK keeps
 * its own server credentials. It is not key material, it cannot be used to move
 * a tag or query Apple directly, and rotating it in NetWatch invalidates every
 * copy immediately.
 *
 * <h3>Which context these preferences belong to</h3>
 * ATAK's, not the plugin's. A plugin context points at the plugin APK's own
 * package, and the ATAK process cannot write there — {@code apply()} fails
 * silently, so saved servers survive in memory for the session and are gone the
 * next time ATAK starts. That was the "I have to re-link my PC every time" bug.
 * The SDK samples all take preferences from {@code mapView.getContext()} for the
 * same reason. A named file keeps these keys out of ATAK's global preferences.
 */
public class ServerBook {

    private static final String TAG = "NetWatchServerBook";
    private static final String PREFS = "netwatch_tak";
    private static final String K_SERVERS = "servers";
    private static final String K_ACTIVE = "active";

    private final SharedPreferences prefs;
    private final List<Server> servers = new ArrayList<>();
    private int active = -1;

    /**
     * @param atakContext ATAK's context ({@code mapView.getContext()}), NOT the
     *                    plugin context — see the note above.
     */
    public ServerBook(Context atakContext) {
        this.prefs = atakContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        load();
    }

    private void load() {
        servers.clear();
        try {
            JSONArray arr = new JSONArray(prefs.getString(K_SERVERS, "[]"));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.optJSONObject(i);
                if (o != null) {
                    Server s = Server.fromJson(o);
                    if (s.isUsable()) {
                        servers.add(s);
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "could not read saved servers", e);
        }
        active = prefs.getInt(K_ACTIVE, servers.isEmpty() ? -1 : 0);
        if (active >= servers.size()) {
            active = servers.isEmpty() ? -1 : 0;
        }
    }

    private void persist() {
        JSONArray arr = new JSONArray();
        for (Server s : servers) {
            try {
                arr.put(s.toJson());
            } catch (Exception e) {
                Log.w(TAG, "could not save " + s.host, e);
            }
        }
        prefs.edit().putString(K_SERVERS, arr.toString()).putInt(K_ACTIVE, active).apply();
    }

    public List<Server> all() {
        return new ArrayList<>(servers);
    }

    public boolean isEmpty() {
        return servers.isEmpty();
    }

    public Server active() {
        return (active >= 0 && active < servers.size()) ? servers.get(active) : null;
    }

    public int activeIndex() {
        return active;
    }

    public void setActive(int index) {
        if (index >= 0 && index < servers.size()) {
            active = index;
            persist();
        }
    }

    /**
     * Add a server, or update the one already on that host:port, and select it.
     *
     * <p>Updating rather than appending matters: re-running discovery or pasting a
     * fresh link after the code was rotated should fix the existing entry, not
     * leave a stale duplicate that fails to connect.
     */
    public void addOrUpdate(Server s) {
        if (s == null || !s.isUsable()) {
            return;
        }
        for (int i = 0; i < servers.size(); i++) {
            if (servers.get(i).sameEndpoint(s)) {
                Server existing = servers.get(i);
                // Keep a code we already have if the new entry has none, so a
                // discovery result does not wipe a working pairing.
                if (s.code == null || s.code.isEmpty()) {
                    s.code = existing.code;
                }
                if (s.label == null || s.label.isEmpty()) {
                    s.label = existing.label;
                }
                servers.set(i, s);
                active = i;
                persist();
                return;
            }
        }
        servers.add(s);
        active = servers.size() - 1;
        persist();
    }

    public void remove(int index) {
        if (index < 0 || index >= servers.size()) {
            return;
        }
        servers.remove(index);
        if (servers.isEmpty()) {
            active = -1;
        } else if (active >= servers.size()) {
            active = servers.size() - 1;
        }
        persist();
    }

    public void setActiveCode(String code) {
        Server s = active();
        if (s != null) {
            s.code = Server.normaliseCode(code);
            persist();
        }
    }

    // -- plugin-wide view options ----------------------------------------
    // Kept beside the servers because they are all the same kind of thing: what
    // this operator chose last time.

    public boolean getBool(String key, boolean def) {
        return prefs.getBoolean(key, def);
    }

    public void putBool(String key, boolean v) {
        prefs.edit().putBoolean(key, v).apply();
    }

    public int getInt(String key, int def) {
        return prefs.getInt(key, def);
    }

    public void putInt(String key, int v) {
        prefs.edit().putInt(key, v).apply();
    }

    public String getString(String key, String def) {
        return prefs.getString(key, def);
    }

    public void putString(String key, String v) {
        prefs.edit().putString(key, v).apply();
    }
}
