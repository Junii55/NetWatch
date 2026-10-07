
package com.atakmap.android.netwatch.net;

import com.atakmap.coremap.log.Log;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Finds NetWatch on the local network so nobody has to type an IP address.
 *
 * <p>How it works: broadcast one small UDP probe on port 8788, then listen
 * briefly for replies. Every NetWatch with its bridge switched on answers with
 * its hostname and bridge port. The reply deliberately does <b>not</b> contain
 * the pair code — handing a credential to anything that asks for it would make
 * the code pointless — so the operator still enters that once.
 *
 * <p><b>Known limit, by design:</b> broadcast does not cross a VPN. Tailscale and
 * WireGuard have no broadcast domain, and most routers will not forward
 * broadcast between subnets, so a tablet on a VPN will find nothing here no
 * matter how long it waits. That is what the pairing link is for, and why the UI
 * never presents discovery as the only way in.
 */
public final class Discovery {

    private static final String TAG = "NetWatchDiscovery";
    private static final int PORT = 8788;
    private static final byte[] PROBE = "NETWATCH-DISCOVER?".getBytes(StandardCharsets.UTF_8);

    /** How long to listen. Long enough for a sleepy tablet radio, short enough to feel instant. */
    private static final int LISTEN_MS = 1500;

    public static class Found {
        public String host = "";
        public int port = Server.DEFAULT_PORT;
        public String name = "";
        public String version = "";

        public Server toServer() {
            return new Server(name, host, port, "");
        }
    }

    private Discovery() {
    }

    /**
     * Probe and collect answers. Blocking — call it off the UI thread.
     *
     * <p>Probes are sent more than once because a single UDP datagram on a busy
     * wifi network is genuinely allowed to vanish, and a discovery feature that
     * works four times in five reads as broken.
     */
    public static List<Found> scan() {
        Map<String, Found> byHost = new LinkedHashMap<>();
        DatagramSocket sock = null;
        try {
            sock = new DatagramSocket();
            sock.setBroadcast(true);
            sock.setSoTimeout(250);

            for (InetAddress target : broadcastTargets()) {
                for (int attempt = 0; attempt < 2; attempt++) {
                    try {
                        sock.send(new DatagramPacket(PROBE, PROBE.length, target, PORT));
                    } catch (Exception e) {
                        Log.d(TAG, "probe to " + target + " failed: " + e.getMessage());
                    }
                }
            }

            long deadline = System.currentTimeMillis() + LISTEN_MS;
            byte[] buf = new byte[1024];
            while (System.currentTimeMillis() < deadline) {
                DatagramPacket in = new DatagramPacket(buf, buf.length);
                try {
                    sock.receive(in);
                } catch (SocketTimeoutException e) {
                    continue;
                }
                Found f = parse(in);
                if (f != null) {
                    byHost.put(f.host + ":" + f.port, f);
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "discovery failed", e);
        } finally {
            if (sock != null) {
                sock.close();
            }
        }
        return new ArrayList<>(byHost.values());
    }

    private static Found parse(DatagramPacket in) {
        try {
            String text = new String(in.getData(), in.getOffset(), in.getLength(),
                    StandardCharsets.UTF_8).trim();
            if (!text.startsWith("{")) {
                return null;
            }
            JSONObject o = new JSONObject(text);
            if (!"NetWatch".equals(o.optString("product"))) {
                return null;
            }
            int port = o.optInt("port", 0);
            if (port < 1 || port > 65535) {
                // Nothing useful to connect to. Drop it rather than offering the
                // operator a choice that cannot work.
                Log.d(TAG, "ignoring a reply advertising port " + port);
                return null;
            }
            Found f = new Found();
            // Trust the packet's source address, not any address inside the
            // payload: the sender cannot lie about where the datagram came from
            // as easily as it can lie about a JSON field, and that is the
            // address we actually have a route to.
            f.host = in.getAddress().getHostAddress();
            f.port = port;
            f.name = o.optString("host", "");
            f.version = o.optString("version", "");
            return f;
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Where to send the probe.
     *
     * <p>255.255.255.255 is the limited broadcast address and is enough on most
     * networks. Each interface's own directed broadcast is added as well, because
     * some Android builds and some APs drop the limited form.
     */
    private static List<InetAddress> broadcastTargets() {
        List<InetAddress> out = new ArrayList<>();
        try {
            out.add(InetAddress.getByName("255.255.255.255"));
        } catch (Exception ignored) {
            // Nothing to do; the per-interface addresses below may still work.
        }
        try {
            java.util.Enumeration<java.net.NetworkInterface> ifaces =
                    java.net.NetworkInterface.getNetworkInterfaces();
            while (ifaces != null && ifaces.hasMoreElements()) {
                java.net.NetworkInterface ni = ifaces.nextElement();
                if (ni.isLoopback() || !ni.isUp()) {
                    continue;
                }
                for (java.net.InterfaceAddress ia : ni.getInterfaceAddresses()) {
                    InetAddress b = ia.getBroadcast();
                    if (b != null && !out.contains(b)) {
                        out.add(b);
                    }
                }
            }
        } catch (Exception e) {
            Log.d(TAG, "interface enumeration failed: " + e.getMessage());
        }
        return out;
    }
}
