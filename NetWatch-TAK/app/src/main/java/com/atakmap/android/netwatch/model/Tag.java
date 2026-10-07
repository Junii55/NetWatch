
package com.atakmap.android.netwatch.model;

import android.graphics.Color;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * One NetWatch tag as the bridge describes it.
 *
 * <p>A tag with no location yet is still a tag: the operator knows they own it
 * and needs to see that it exists and is waiting, rather than wonder why their
 * list is short. {@link #hasFix()} is the gate for anything that needs
 * coordinates.
 */
public class Tag {

    /** Opaque, stable identifier from the bridge. Also the CoT uid. */
    public String uid = "";
    /** NetWatch's own numeric id, needed to ask for this tag's history. */
    public int id = -1;
    public String name = "";
    /** Hex colour chosen in the desktop app, e.g. {@code #2dd4bf}. */
    public String color = "#2dd4bf";
    /** Whether the desktop app has breadcrumbs switched on for this tag. */
    public boolean showTrack = true;
    /**
     * Whether the tag is shown at all. Hiding a tag in the NetWatch desktop app
     * hides it here too, so the two agree instead of quietly disagreeing.
     */
    public boolean visible = true;
    public String notes = "";

    public double lat = Double.NaN;
    public double lon = Double.NaN;
    /** Seconds since the fix was recorded, as the bridge calculated it. */
    public double ageS = -1;
    public double accuracy = -1;
    public int confidence = -1;
    public int reports = 0;

    /** Breadcrumbs, oldest first. Empty until history is requested. */
    public final List<double[]> track = new ArrayList<>();

    public boolean hasFix() {
        return !Double.isNaN(lat) && !Double.isNaN(lon)
                && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180
                // 0,0 is in the Gulf of Guinea and is almost always a parse
                // failure rather than a tag that really went there.
                && !(lat == 0 && lon == 0);
    }

    public int argb() {
        try {
            return Color.parseColor(color.startsWith("#") ? color : "#" + color);
        } catch (IllegalArgumentException e) {
            return Color.parseColor("#2dd4bf");
        }
    }

    /** "4m", "3h", "2d" — short enough for a list row on a tablet. */
    public String age() {
        if (ageS < 0) {
            return "no fix yet";
        }
        long s = (long) ageS;
        if (s < 90) {
            return s + "s";
        }
        if (s < 3600) {
            return (s / 60) + "m";
        }
        if (s < 86400) {
            return (s / 3600) + "h";
        }
        return (s / 86400) + "d";
    }

    public String coords() {
        if (!hasFix()) {
            return "waiting for a fix";
        }
        return String.format(Locale.US, "%.5f, %.5f", lat, lon);
    }

    /** The second line of a list row. */
    public String subtitle() {
        StringBuilder sb = new StringBuilder();
        sb.append(age());
        if (hasFix()) {
            sb.append("  ·  ").append(coords());
            if (accuracy >= 0) {
                sb.append("  ·  ±").append((long) accuracy).append(" m");
            }
        }
        if (reports > 0) {
            sb.append("  ·  ").append(reports)
              .append(reports == 1 ? " report" : " reports");
        }
        return sb.toString();
    }
}
