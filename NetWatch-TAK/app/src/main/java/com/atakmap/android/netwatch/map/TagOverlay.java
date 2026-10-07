
package com.atakmap.android.netwatch.map;

import com.atakmap.android.maps.MapGroup;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.maps.Marker;
import com.atakmap.android.maps.Polyline;
import com.atakmap.android.netwatch.model.Tag;
import com.atakmap.coremap.log.Log;
import com.atakmap.coremap.maps.coords.GeoPoint;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Draws tags and their breadcrumb tracks on the ATAK map.
 *
 * <p>Everything goes into a child group named "NetWatch" rather than straight into
 * the root group. That costs one line and buys the operator a single checkbox in
 * Overlay Manager that hides every tag at once — which is what someone wants
 * when a map gets busy, and which the previous plugin could not offer because it
 * put markers in the root.
 *
 * <p>Markers are reused across refreshes and keyed by the tag's stable uid, so a
 * tag moves instead of leaving a trail of duplicates behind.
 */
public class TagOverlay {

    private static final String TAG = "NetWatchOverlay";
    private static final String GROUP = "NetWatch";

    /**
     * CoT types offered for the marker. The type is what picks the icon in ATAK,
     * so this is the icon chooser.
     */
    public static final String[] ICON_LABELS = {
            "Sensor", "Ground unit", "Vehicle", "Equipment",
            "Person", "Waypoint", "Point of interest", "Incident",
    };
    public static final String[] ICON_TYPES = {
            "a-f-G-E-S",
            "a-f-G-U-C",
            "a-f-G-E-V",
            "a-f-G-E",
            "a-f-G-U-C-I",
            "b-m-p-w",
            "b-m-p-s-p",
            "a-h-G",
    };

    private final MapView mapView;
    private final Map<String, Marker> markers = new HashMap<>();
    private final Map<String, Polyline> tracks = new HashMap<>();
    private MapGroup group;
    private String iconType = ICON_TYPES[0];

    public TagOverlay(MapView mapView) {
        this.mapView = mapView;
    }

    private MapGroup group() {
        if (group == null) {
            try {
                MapGroup root = mapView.getRootGroup();
                MapGroup existing = root.findMapGroup(GROUP);
                group = existing != null ? existing : root.addGroup(GROUP);
            } catch (Throwable t) {
                Log.w(TAG, "could not create the NetWatch map group", t);
                group = mapView.getRootGroup();
            }
        }
        return group;
    }

    /** The CoT type currently chosen, so shared events can carry the same icon. */
    public String iconType() {
        return iconType;
    }

    public void setIconIndex(int index) {
        if (index < 0 || index >= ICON_TYPES.length) {
            index = 0;
        }
        iconType = ICON_TYPES[index];
        for (Marker m : markers.values()) {
            try {
                m.setType(iconType);
            } catch (Throwable t) {
                Log.w(TAG, "setType", t);
            }
        }
    }

    /**
     * Bring the map in line with {@code tags}.
     *
     * @param showMarkers draw tag positions at all
     * @param showTracks  draw breadcrumbs, for tags that carry them
     * @param respectPerTag honour each tag's own breadcrumb setting from the
     *                      desktop app, instead of overriding it here
     */
    public void sync(List<Tag> tags, boolean showMarkers, boolean showTracks,
                     boolean respectPerTag) {
        Set<String> keepMarkers = new HashSet<>();
        Set<String> keepTracks = new HashSet<>();

        if (tags != null) {
            for (Tag t : tags) {
                if (!t.visible) {
                    // Hidden in NetWatch means hidden here. Falling through
                    // would leave it drawn, and the operator would have no way
                    // to work out why.
                    continue;
                }
                String uid = uidOf(t);
                if (showMarkers && t.hasFix()) {
                    keepMarkers.add(uid);
                    upsertMarker(uid, t);
                }
                boolean wanted = showTracks && (!respectPerTag || t.showTrack);
                if (wanted && t.track.size() >= 2) {
                    keepTracks.add(uid);
                    upsertTrack(uid, t);
                }
            }
        }

        prune(markers, keepMarkers);
        prune(tracks, keepTracks);
    }

    private String uidOf(Tag t) {
        if (t.uid != null && !t.uid.isEmpty()) {
            return t.uid;
        }
        // Only reachable against an older bridge that did not send a uid.
        return "netwatch-local-" + (t.id >= 0 ? String.valueOf(t.id) : t.name);
    }

    private void upsertMarker(String uid, Tag t) {
        try {
            GeoPoint gp = new GeoPoint(t.lat, t.lon);
            Marker m = markers.get(uid);
            if (m == null) {
                m = new Marker(gp, uid);
                markers.put(uid, m);
                group().addItem(m);
            } else {
                m.setPoint(gp);
            }
            m.setTitle(t.name);
            m.setType(iconType);
            m.setMetaString("callsign", t.name);
            m.setMetaString("netwatch.uid", uid);
            m.setMetaString("remarks", remarks(t));
            m.setColor(t.argb());
            // Not a self-marker and not something the operator should drag into
            // a wrong position by accident.
            m.setMetaBoolean("movable", false);
            m.setMetaBoolean("editable", false);
            // Do not persist into ATAK's state saver: NetWatch is the source of
            // truth, and a stale marker surviving a restart would claim a tag is
            // somewhere it was hours ago.
            m.setMetaBoolean("archive", false);
        } catch (Throwable t2) {
            Log.e(TAG, "marker for " + t.name, t2);
        }
    }

    private String remarks(Tag t) {
        StringBuilder sb = new StringBuilder("NetWatch tag");
        sb.append("\nLast report: ").append(t.age()).append(" ago");
        if (t.accuracy >= 0) {
            sb.append(String.format(Locale.US, "\nAccuracy: ±%.0f m", t.accuracy));
        }
        if (t.confidence >= 0) {
            sb.append("\nConfidence: ").append(t.confidence);
        }
        if (t.notes != null && !t.notes.isEmpty()) {
            sb.append("\n").append(t.notes);
        }
        sb.append("\n\nLocated through Apple's Find My network. Position is where a "
                + "passing iPhone last heard this tag, not live GPS.");
        return sb.toString();
    }

    private void upsertTrack(String uid, Tag t) {
        try {
            GeoPoint[] pts = new GeoPoint[t.track.size()];
            for (int i = 0; i < pts.length; i++) {
                double[] p = t.track.get(i);
                pts[i] = new GeoPoint(p[0], p[1]);
            }
            Polyline line = tracks.get(uid);
            if (line == null) {
                line = new Polyline(uid + "-track");
                tracks.put(uid, line);
                line.setMetaBoolean("movable", false);
                line.setMetaBoolean("editable", false);
                line.setMetaBoolean("archive", false);
                group().addItem(line);
            }
            line.setPoints(pts);
            line.setTitle(t.name + " track");
            line.setStrokeColor(t.argb());
            line.setStrokeWeight(3.0d);
        } catch (Throwable e) {
            Log.e(TAG, "track for " + t.name, e);
        }
    }

    private <T extends com.atakmap.android.maps.MapItem> void prune(
            Map<String, T> live, Set<String> keep) {
        List<String> drop = new ArrayList<>();
        for (String uid : live.keySet()) {
            if (!keep.contains(uid)) {
                drop.add(uid);
            }
        }
        for (String uid : drop) {
            T item = live.remove(uid);
            if (item == null) {
                continue;
            }
            try {
                if (item.getGroup() != null) {
                    item.getGroup().removeItem(item);
                } else {
                    group().removeItem(item);
                }
            } catch (Throwable t) {
                Log.w(TAG, "remove " + uid, t);
            }
        }
    }

    /** Centre the map on a tag. */
    public void panTo(Tag t) {
        if (t == null || !t.hasFix()) {
            return;
        }
        try {
            mapView.getMapController().panTo(new GeoPoint(t.lat, t.lon), true);
        } catch (Throwable e) {
            Log.e(TAG, "panTo", e);
        }
    }

    /** Fit every tag that has a position into view. */
    public void panToAll(List<Tag> tags) {
        double minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
        int n = 0;
        for (Tag t : tags) {
            if (!t.hasFix()) {
                continue;
            }
            n++;
            minLat = Math.min(minLat, t.lat);
            maxLat = Math.max(maxLat, t.lat);
            minLon = Math.min(minLon, t.lon);
            maxLon = Math.max(maxLon, t.lon);
        }
        if (n == 0) {
            return;
        }
        try {
            mapView.getMapController().panTo(
                    new GeoPoint((minLat + maxLat) / 2, (minLon + maxLon) / 2), true);
        } catch (Throwable e) {
            Log.e(TAG, "panToAll", e);
        }
    }

    /** Remove everything this plugin drew. */
    public void clear() {
        prune(markers, new HashSet<String>());
        prune(tracks, new HashSet<String>());
    }

    public void dispose() {
        clear();
        try {
            if (group != null && group != mapView.getRootGroup()) {
                MapGroup parent = group.getParentGroup();
                if (parent != null) {
                    parent.removeGroup(group);
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not remove the NetWatch map group", t);
        }
        group = null;
    }
}
