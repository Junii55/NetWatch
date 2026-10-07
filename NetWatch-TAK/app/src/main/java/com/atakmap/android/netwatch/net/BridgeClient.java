
package com.atakmap.android.netwatch.net;

import com.atakmap.android.netwatch.model.Tag;
import com.atakmap.coremap.log.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * Talks to the NetWatch desktop's ATAK bridge.
 *
 * <p>Plain {@link HttpURLConnection} on purpose: an ATAK plugin that drags in an
 * HTTP library has to get that library past the pipeline's scanner too, and the
 * whole surface here is five GETs and one POST.
 *
 * <p>Errors are returned in {@link Result#error} rather than thrown, and they are
 * written for the operator standing in a field, not for a log file. "Wrong pair
 * code" and "NetWatch is not running" need different actions, so they get
 * different messages.
 */
public class BridgeClient {

    private static final String TAG = "NetWatchBridge";
    private static final int CONNECT_MS = 4000;
    private static final int READ_MS = 12000;
    private static final int SYNC_READ_MS = 90000;

    public static class Result {
        public final List<Tag> tags = new ArrayList<>();
        /** Null when the call succeeded. */
        public String error;
        public int http;
        /** Set when the failure is specifically a rejected pair code. */
        public boolean unauthorised;
        public String serverName = "";
        public String version = "";
        public int syncedTags = -1;
        public int newReports = -1;
    }

    private final Server server;

    public BridgeClient(Server server) {
        this.server = server;
    }

    /** Cheap identity probe that needs no pair code. */
    public Result hello() {
        Result out = new Result();
        JSONObject o = request("/api/hello", "GET", out, READ_MS, false);
        if (o != null) {
            out.serverName = o.optString("host", "");
            out.version = o.optString("version", "");
            if (!"NetWatch".equals(o.optString("product"))) {
                out.error = "Something is answering on that port, but it is not NetWatch.";
            }
        }
        return out;
    }

    /** Current position of every tag. */
    public Result fixes() {
        Result out = new Result();
        JSONObject status = request("/api/status", "GET", out, READ_MS, true);
        if (status != null) {
            out.serverName = status.optString("host", "");
            out.version = status.optString("version", "");
        }
        if (out.error != null) {
            return out;
        }
        JSONObject o = request("/api/fixes", "GET", out, READ_MS, true);
        if (o != null) {
            parseFixes(o.optJSONArray("fixes"), out);
        }
        return out;
    }

    /**
     * Breadcrumbs for every tag in one request.
     *
     * @param from inclusive ISO date (yyyy-MM-dd) or null for no lower bound
     * @param to   inclusive ISO date or null
     */
    public Result tracks(String from, String to, int limit) {
        Result out = new Result();
        StringBuilder p = new StringBuilder("/api/tracks?limit=").append(limit);
        if (from != null && !from.isEmpty()) {
            p.append("&from=").append(enc(from));
        }
        if (to != null && !to.isEmpty()) {
            p.append("&to=").append(enc(to));
        }
        JSONObject o = request(p.toString(), "GET", out, READ_MS, true);
        if (o == null) {
            return out;
        }
        JSONArray arr = o.optJSONArray("tracks");
        for (int i = 0; arr != null && i < arr.length(); i++) {
            JSONObject t = arr.optJSONObject(i);
            if (t == null) {
                continue;
            }
            Tag tag = new Tag();
            tag.uid = t.optString("uid", "");
            tag.id = t.optInt("id", -1);
            tag.name = t.optString("name", "");
            tag.color = t.optString("color", "#2dd4bf");
            tag.showTrack = t.optBoolean("show_track", true);
            tag.visible = t.optBoolean("visible", true);
            JSONArray pts = t.optJSONArray("points");
            for (int j = 0; pts != null && j < pts.length(); j++) {
                JSONObject pt = pts.optJSONObject(j);
                if (pt == null || pt.isNull("lat") || pt.isNull("lon")) {
                    continue;
                }
                tag.track.add(new double[] { pt.optDouble("lat"), pt.optDouble("lon") });
            }
            out.tags.add(tag);
        }
        return out;
    }

    /**
     * Ask NetWatch to fetch new reports from Apple.
     *
     * <p>The bridge rate-limits this and answers 409 if it was called too
     * recently; that is reported as-is because it is a normal, informative
     * answer rather than a fault.
     */
    public Result sync() {
        Result out = new Result();
        JSONObject o = request("/api/poll", "POST", out, SYNC_READ_MS, true);
        if (o != null) {
            out.syncedTags = o.optInt("synced", -1);
            out.newReports = o.optInt("new_reports", -1);
            parseFixes(o.optJSONArray("fixes"), out);
        }
        return out;
    }

    /**
     * Live CoT, one event per tag that has a position.
     *
     * @param cotType the CoT type to stamp on each event, which decides the icon
     *                a TAK client draws. Passed through so a shared tag looks the
     *                same to the team as it does here.
     */
    public List<String> cotEvents(int staleSeconds, String cotType, Result out) {
        List<String> events = new ArrayList<>();
        JSONObject o = request("/api/cot?stale=" + staleSeconds
                + "&type=" + enc(cotType), "GET", out, READ_MS, true);
        JSONArray arr = o == null ? null : o.optJSONArray("events");
        for (int i = 0; arr != null && i < arr.length(); i++) {
            String ev = arr.optString(i, "");
            if (!ev.isEmpty()) {
                events.add(ev);
            }
        }
        return events;
    }

    private void parseFixes(JSONArray arr, Result out) {
        for (int i = 0; arr != null && i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) {
                continue;
            }
            Tag t = new Tag();
            t.uid = o.optString("uid", "");
            t.id = o.optInt("id", -1);
            t.name = o.optString("name", "");
            t.color = o.optString("color", "#2dd4bf");
            t.showTrack = o.optBoolean("show_track", true);
            t.visible = o.optBoolean("visible", true);
            t.notes = o.optString("notes", "");
            t.reports = o.optInt("reports", 0);
            // A tag with no report yet sends JSON null, not 0: optDouble would
            // turn that into 0.0 and plant the tag off the coast of Africa.
            if (!o.isNull("lat") && !o.isNull("lon")) {
                t.lat = o.optDouble("lat", Double.NaN);
                t.lon = o.optDouble("lon", Double.NaN);
            }
            t.ageS = o.isNull("age_s") ? -1 : o.optDouble("age_s", -1);
            t.accuracy = o.isNull("accuracy") ? -1 : o.optDouble("accuracy", -1);
            t.confidence = o.isNull("confidence") ? -1 : o.optInt("confidence", -1);
            out.tags.add(t);
        }
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (Exception e) {
            return s;
        }
    }

    private JSONObject request(String path, String method, Result out,
                              int readMs, boolean withCode) {
        if (server == null || !server.isUsable()) {
            out.error = "No NetWatch server set up yet.";
            return null;
        }
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(server.baseUrl() + path).openConnection();
            c.setConnectTimeout(CONNECT_MS);
            c.setReadTimeout(readMs);
            c.setRequestMethod(method);
            c.setRequestProperty("Accept", "application/json");
            if (withCode) {
                c.setRequestProperty("X-NetWatch-Pair", server.code);
            }
            if ("POST".equals(method)) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                byte[] body = "{}".getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(body.length);
                OutputStream os = c.getOutputStream();
                os.write(body);
                os.close();
            }

            int code = c.getResponseCode();
            out.http = code;
            String text = readAll(code >= 400 ? c.getErrorStream() : c.getInputStream());

            if (code == 401) {
                out.unauthorised = true;
                out.error = "NetWatch refused the pair code. Check it in "
                        + "NetWatch › ATAK bridge.";
                return null;
            }
            JSONObject o = null;
            if (text != null && text.trim().startsWith("{")) {
                o = new JSONObject(text);
            }
            if (code >= 400) {
                String msg = o == null ? null : o.optString("error", null);
                out.error = msg != null && !msg.isEmpty()
                        ? msg : ("NetWatch answered HTTP " + code + ".");
                return null;
            }
            if (o == null) {
                out.error = "That address answered, but not with NetWatch data.";
                return null;
            }
            return o;
        } catch (java.net.SocketTimeoutException e) {
            out.error = "No answer from " + server.host + ":" + server.port
                    + ". Is the PC awake and on the same network?";
            return null;
        } catch (java.net.ConnectException e) {
            out.error = "Nothing is listening on " + server.host + ":" + server.port
                    + ". Switch the ATAK bridge on in NetWatch.";
            return null;
        } catch (java.net.UnknownHostException e) {
            out.error = "Cannot find " + server.host + " on the network.";
            return null;
        } catch (Exception e) {
            Log.w(TAG, method + " " + path, e);
            out.error = e.getMessage() == null ? e.toString() : e.getMessage();
            return null;
        } finally {
            if (c != null) {
                c.disconnect();
            }
        }
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) {
            return "";
        }
        BufferedReader br = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8));
        try {
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = br.readLine()) != null) {
                sb.append(line);
            }
            return sb.toString();
        } finally {
            br.close();
        }
    }
}
