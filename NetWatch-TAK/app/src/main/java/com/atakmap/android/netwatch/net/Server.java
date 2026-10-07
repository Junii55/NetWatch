
package com.atakmap.android.netwatch.net;

import android.text.TextUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

/**
 * Where a NetWatch desktop lives and the code that opens it.
 *
 * <p>Nothing here is baked in. The old NetWatch plugin shipped a hardcoded list
 * of its author's own addresses, which is fine for one person and useless to
 * anyone else — every customer runs NetWatch on their own machine, behind their
 * own VPN, on whatever subnet they happen to have. So a server is always
 * something the operator added: by discovery, by pasting a pairing link, or by
 * typing a host.
 */
public class Server {

    public static final int DEFAULT_PORT = 8787;

    public String label = "";
    public String host = "";
    public int port = DEFAULT_PORT;
    public String code = "";

    public Server() {
    }

    public Server(String label, String host, int port, String code) {
        this.label = label == null ? "" : label;
        this.host = host == null ? "" : host.trim();
        this.port = port <= 0 ? DEFAULT_PORT : port;
        this.code = normaliseCode(code);
    }

    public boolean isUsable() {
        return !TextUtils.isEmpty(host);
    }

    public String baseUrl() {
        return "http://" + host + ":" + port;
    }

    /** What to show in the picker: the operator's label, else the address. */
    public String display() {
        if (!TextUtils.isEmpty(label)) {
            return label + "  (" + host + ")";
        }
        return host + ":" + port;
    }

    /**
     * Normalise a pair code the way the desktop does: strip separators, upper
     * case, regroup in fours.
     *
     * <p>Deliberately no "O means Q" style guessing. NetWatch builds codes from
     * an alphabet with no confusable characters in it, so a remap could only
     * ever turn one wrong code into a different wrong code.
     */
    public static String normaliseCode(String raw) {
        if (raw == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        for (char c : raw.toUpperCase(Locale.US).toCharArray()) {
            if (Character.isLetterOrDigit(c)) {
                sb.append(c);
            }
        }
        String body = sb.length() > 16 ? sb.substring(0, 16) : sb.toString();
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < body.length(); i++) {
            if (i > 0 && i % 4 == 0) {
                out.append('-');
            }
            out.append(body.charAt(i));
        }
        return out.toString();
    }

    /**
     * Parse the pairing link NetWatch shows under the bridge switch.
     *
     * <p>{@code netwatch://pair?h=<host>&p=<port>&c=<code>&n=<name>}
     *
     * <p>This exists because discovery cannot cross a VPN: Tailscale and
     * WireGuard have no broadcast domain, so a tablet on the VPN will never hear
     * a broadcast probe reply. One pasted string carries the host, the port and
     * the code together, which is also three fewer things to mistype.
     *
     * @return the parsed server, or null if this was not a pairing link.
     */
    public static Server fromLink(String link) {
        if (link == null) {
            return null;
        }
        String s = link.trim();
        int q = s.indexOf('?');
        if (q < 0 || !s.toLowerCase(Locale.US).startsWith("netwatch://")) {
            return null;
        }
        Server out = new Server();
        for (String pair : s.substring(q + 1).split("&")) {
            int eq = pair.indexOf('=');
            if (eq <= 0) {
                continue;
            }
            String k = pair.substring(0, eq);
            String v = urlDecode(pair.substring(eq + 1));
            switch (k) {
                case "h": out.host = v.trim(); break;
                case "p": out.port = parsePort(v); break;
                case "c": out.code = normaliseCode(v); break;
                case "n": out.label = v.trim(); break;
                default: break;
            }
        }
        return out.isUsable() ? out : null;
    }

    private static String urlDecode(String v) {
        try {
            return java.net.URLDecoder.decode(v, "UTF-8");
        } catch (Exception e) {
            return v;
        }
    }

    public static int parsePort(String v) {
        try {
            int p = Integer.parseInt(v.trim());
            return (p >= 1 && p <= 65535) ? p : DEFAULT_PORT;
        } catch (Exception e) {
            return DEFAULT_PORT;
        }
    }

    /**
     * Accept what an operator typed into the host box: a bare host, host:port,
     * or a full URL. Getting this wrong is the single most common setup failure,
     * so be generous about the shape.
     */
    public static Server fromHostField(String typed, int fallbackPort, String code) {
        if (typed == null) {
            return null;
        }
        String s = typed.trim();
        if (TextUtils.isEmpty(s)) {
            return null;
        }
        Server link = fromLink(s);
        if (link != null) {
            return link;
        }
        s = s.replaceFirst("(?i)^https?://", "");
        int slash = s.indexOf('/');
        if (slash >= 0) {
            s = s.substring(0, slash);
        }
        int port = fallbackPort;
        // Rightmost colon only, so an IPv6 literal in brackets is left alone.
        int colon = s.lastIndexOf(':');
        if (colon > 0 && !s.endsWith("]")) {
            port = parsePort(s.substring(colon + 1));
            s = s.substring(0, colon);
        }
        return s.isEmpty() ? null : new Server("", s, port, code);
    }

    public JSONObject toJson() throws JSONException {
        JSONObject o = new JSONObject();
        o.put("label", label);
        o.put("host", host);
        o.put("port", port);
        o.put("code", code);
        return o;
    }

    public static Server fromJson(JSONObject o) {
        return new Server(o.optString("label", ""), o.optString("host", ""),
                o.optInt("port", DEFAULT_PORT), o.optString("code", ""));
    }

    /** Same machine and port: used to avoid saving a duplicate. */
    public boolean sameEndpoint(Server other) {
        return other != null && port == other.port
                && host.equalsIgnoreCase(other.host);
    }
}
