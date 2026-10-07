
package com.atakmap.android.netwatch.map;

import com.atakmap.coremap.log.Log;

import java.lang.reflect.Method;
import java.util.List;

/**
 * Publishes tags as CoT so they are not stuck inside this one tablet.
 *
 * <p>Two destinations, and the difference matters:
 *
 * <ul>
 *   <li><b>Local</b> — the internal dispatcher. The event enters this ATAK as if
 *       it had arrived from the network, so other plugins and tools can see the
 *       tag. Nothing leaves the device.</li>
 *   <li><b>Share</b> — the external dispatcher. The event goes out over whatever
 *       TAK Server or mesh this ATAK is already connected to, so everyone on the
 *       team sees the tag on their own map.</li>
 * </ul>
 *
 * <p>Note what is <i>not</i> here: any server address, certificate or port. The
 * plugin never configures a TAK connection and does not need to know whether the
 * operator runs a server on a Raspberry Pi, a cloud host, or nothing at all. It
 * hands the event to ATAK and ATAK routes it over the connection the operator
 * already set up. That is the only way this can work unchanged for someone
 * else's infrastructure.
 *
 * <h3>Why reflection</h3>
 * The dispatcher lives at {@code com.atakmap.android.cot.CotMapComponent}, and
 * which of its methods are exposed to plugins has moved between ATAK releases —
 * the previous version of this plugin had CoT ripped out entirely after a
 * compile failure against one SDK, reportedly over
 * {@code getInternalDispatcher}, which in fact lives on a different class than
 * the one it was called on. Binding late means a plugin built against one SDK
 * still loads and still shows markers on a release where the method moved, and
 * the operator gets a clear message instead of a crash. The cost is no
 * compile-time check, which is why {@link #isAvailable()} is probed up front and
 * the UI disables the switch when it comes back false.
 */
public final class CotPush {

    private static final String TAG = "NetWatchCot";
    private static final String COT_MAP_COMPONENT = "com.atakmap.android.cot.CotMapComponent";

    private static Boolean available;
    private static Method parse;

    private CotPush() {
    }

    /** True when this ATAK build exposes a usable CoT dispatcher. */
    public static synchronized boolean isAvailable() {
        if (available == null) {
            available = resolve("getInternalDispatcher") != null
                    && cotEventParse() != null;
            if (!available) {
                Log.w(TAG, "no CoT dispatcher on this ATAK build; "
                        + "markers will still work");
            }
        }
        return available;
    }

    /**
     * Send events.
     *
     * @param external true to put them on the TAK network, false to keep them local
     * @return how many were accepted
     */
    public static int send(List<String> events, boolean external) {
        if (events == null || events.isEmpty()) {
            return 0;
        }
        Object dispatcher = resolve(external ? "getExternalDispatcher"
                                             : "getInternalDispatcher");
        if (dispatcher == null) {
            return 0;
        }
        Method parseMethod = cotEventParse();
        if (parseMethod == null) {
            return 0;
        }
        Method dispatch = findDispatch(dispatcher);
        if (dispatch == null) {
            Log.w(TAG, "dispatcher has no dispatch(CotEvent)");
            return 0;
        }

        int sent = 0;
        for (String xml : events) {
            try {
                Object event = parseMethod.invoke(null, xml);
                if (event == null) {
                    continue;
                }
                dispatch.invoke(dispatcher, event);
                sent++;
            } catch (Throwable t) {
                Log.w(TAG, "could not dispatch an event", t);
            }
        }
        return sent;
    }

    private static Object resolve(String getter) {
        try {
            Class<?> cls = Class.forName(COT_MAP_COMPONENT);
            Method m = cls.getMethod(getter);
            return m.invoke(null);
        } catch (Throwable t) {
            Log.d(TAG, COT_MAP_COMPONENT + "." + getter + "() unavailable: " + t);
            return null;
        }
    }

    private static synchronized Method cotEventParse() {
        if (parse == null) {
            try {
                Class<?> cls = Class.forName("com.atakmap.coremap.cot.event.CotEvent");
                parse = cls.getMethod("parse", String.class);
            } catch (Throwable t) {
                Log.d(TAG, "CotEvent.parse unavailable: " + t);
            }
        }
        return parse;
    }

    private static Method findDispatch(Object dispatcher) {
        for (Method m : dispatcher.getClass().getMethods()) {
            if ("dispatch".equals(m.getName()) && m.getParameterTypes().length == 1) {
                return m;
            }
        }
        return null;
    }
}
