
package com.atakmap.android.netwatch;

import android.app.AlertDialog;
import android.app.DatePickerDialog;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import com.atak.plugins.impl.PluginLayoutInflater;
import com.atakmap.android.dropdown.DropDown;
import com.atakmap.android.dropdown.DropDownReceiver;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.netwatch.map.CotPush;
import com.atakmap.android.netwatch.map.TagOverlay;
import com.atakmap.android.netwatch.model.Tag;
import com.atakmap.android.netwatch.net.BridgeClient;
import com.atakmap.android.netwatch.net.Discovery;
import com.atakmap.android.netwatch.net.Server;
import com.atakmap.android.netwatch.net.ServerBook;
import com.atakmap.android.netwatch.plugin.R;
import com.atakmap.coremap.log.Log;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The NetWatch pane.
 *
 * <p>Design intent: the pane shows tags and three buttons. Everything an operator
 * sets once lives in Options, and everything about reaching a computer lives in
 * pairing. The previous version of this plugin put fifteen controls on one panel,
 * which is workable for its author and baffling for anyone else.
 *
 * <p>Threading: every network call runs on a single background executor, and the
 * UI is only ever touched from the main thread. One executor rather than a pool
 * because the calls are naturally sequential and overlapping refreshes would
 * fight over the same marker set.
 */
public class NetWatchDropDownReceiver extends DropDownReceiver
        implements DropDown.OnStateListener {

    public static final String SHOW_PLUGIN = "com.atakmap.android.netwatch.SHOW_PLUGIN";
    private static final String TAG = "NetWatchPane";

    // Saved option keys.
    private static final String K_MARKERS = "show_markers";
    private static final String K_TRACKS = "show_tracks";
    private static final String K_RESPECT = "respect_per_tag";
    private static final String K_ICON = "icon_index";
    private static final String K_AUTO = "auto_refresh";
    private static final String K_INTERVAL = "interval_index";
    private static final String K_COT = "cot_mode";
    private static final String K_FROM = "trail_from";
    private static final String K_TO = "trail_to";

    private static final int[] INTERVAL_SECONDS = { 30, 60, 300, 900 };
    private static final String[] INTERVAL_LABELS = {
            "every 30 seconds", "every minute", "every 5 minutes", "every 15 minutes" };

    private static final int COT_OFF = 0;
    private static final int COT_LOCAL = 1;
    private static final int COT_EXTERNAL = 2;

    /** How long a shared tag stays on a teammate's map before going stale. */
    private static final int COT_STALE_SECONDS = 1800;

    /** Breadcrumb points per tag. Enough for a long history, bounded for a tablet. */
    private static final int TRACK_LIMIT = 3000;

    /**
     * The plugin's own context. Use it for anything that reads this APK's
     * resources: inflating layouts, getString, getResources.
     */
    private final Context pluginCtx;

    /**
     * ATAK's context. Use it for anything that opens a window — dialogs, date
     * pickers, toasts.
     *
     * <p>These are not interchangeable. The plugin context has no activity behind
     * it, so building an AlertDialog on it throws BadTokenException ("token null
     * is not valid") the first time the operator taps a button. The SDK's own
     * samples draw the same line: {@code AlertDialog.Builder(mapView.getContext())}
     * with {@code ArrayAdapter(pluginContext, ...)} inside it.
     */
    private final Context uiCtx;

    private final View root;
    private final TextView statusText;
    private final TextView statusDot;
    private final TextView emptyText;
    private final View setupCard;
    private final View serverRow;
    private final View actionsRow;
    private final Spinner serverSpinner;
    private final ListView listView;
    private final Button fullBtn;

    private final ServerBook book;
    private final TagOverlay overlay;
    private final TagAdapter adapter;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    private final List<Tag> tags = new ArrayList<>();
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            refresh(false);
        }
    };

    private boolean full;
    private boolean disposed;
    /** Guards against a spinner selection callback firing while we populate it. */
    private boolean populatingServers;

    public NetWatchDropDownReceiver(MapView mapView, Context pluginContext) {
        super(mapView);
        this.pluginCtx = pluginContext;
        this.uiCtx = mapView.getContext();
        // ATAK's context, not the plugin's: the plugin context cannot write
        // preferences from inside the ATAK process. See ServerBook.
        this.book = new ServerBook(uiCtx);
        this.overlay = new TagOverlay(mapView);

        root = PluginLayoutInflater.inflate(pluginContext, R.layout.main_layout, null);
        statusText = root.findViewById(R.id.nw_status);
        statusDot = root.findViewById(R.id.nw_dot);
        emptyText = root.findViewById(R.id.nw_empty);
        setupCard = root.findViewById(R.id.nw_setup_card);
        serverRow = root.findViewById(R.id.nw_server_row);
        actionsRow = root.findViewById(R.id.nw_actions);
        serverSpinner = root.findViewById(R.id.nw_server);
        listView = root.findViewById(R.id.nw_list);
        fullBtn = root.findViewById(R.id.nw_full);

        // Set here rather than in the layout: a non-ASCII glyph in an XML
        // attribute survives only if every tool that touches the file agrees on
        // the encoding, and one that reads UTF-8 as ANSI turns a filled circle
        // into "a-" in the title bar. javac resolves this escape itself.
        statusDot.setText("●");

        adapter = new TagAdapter();
        listView.setAdapter(adapter);

        overlay.setIconIndex(book.getInt(K_ICON, 0));

        root.findViewById(R.id.nw_discover).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                runDiscovery();
            }
        });
        root.findViewById(R.id.nw_paste).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openPairDialog(null, true);
            }
        });
        root.findViewById(R.id.nw_manual).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openPairDialog(null, false);
            }
        });
        root.findViewById(R.id.nw_servers_edit).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openServerManager();
            }
        });
        root.findViewById(R.id.nw_settings).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openOptions();
            }
        });
        root.findViewById(R.id.nw_refresh).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                refresh(true);
            }
        });
        root.findViewById(R.id.nw_sync).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                syncNow();
            }
        });
        root.findViewById(R.id.nw_center).setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                overlay.panToAll(tags);
            }
        });
        fullBtn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                toggleFull();
            }
        });

        serverSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onItemSelected(AdapterView<?> parent, View view, int pos, long id) {
                if (populatingServers || pos == book.activeIndex()) {
                    return;
                }
                book.setActive(pos);
                tags.clear();
                adapter.notifyDataSetChanged();
                overlay.clear();
                refresh(true);
            }

            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        syncChrome();
    }

    // -- drop-down plumbing ----------------------------------------------

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !SHOW_PLUGIN.equals(intent.getAction())) {
            return;
        }
        if (!isVisible()) {
            openPane(false);
            // Opening the pane is the moment the operator wants current data.
            if (book.active() != null) {
                refresh(true);
            }
        }
    }

    private void openPane(boolean wantFull) {
        full = wantFull;
        fullBtn.setText(full ? "Shrink" : "Expand");
        if (full) {
            showDropDown(root, FULL_WIDTH, FULL_HEIGHT, FULL_WIDTH, FULL_HEIGHT, false, this);
        } else {
            showDropDown(root, HALF_WIDTH, FULL_HEIGHT, FULL_WIDTH, HALF_HEIGHT, false, this);
        }
    }

    private void toggleFull() {
        if (!isVisible()) {
            openPane(true);
            return;
        }
        full = !full;
        fullBtn.setText(full ? "Shrink" : "Expand");
        try {
            applySize();
        } catch (Throwable t) {
            openPane(full);
        }
    }

    /**
     * Resize to the current state, in the shape that suits the orientation.
     *
     * <p>{@link #showDropDown} is handed a landscape pair <i>and</i> a portrait
     * pair and picks between them itself. {@link #resize} is not — it takes one
     * width and one height and applies them as given. Passing the landscape
     * shape (half width, full height) unconditionally is what put the pane in a
     * narrow left-hand column on a phone held upright: correct in landscape,
     * wrong in portrait, which is why Expand looked fine and Shrink did not.
     *
     * <p>Half means half the width beside the map in landscape, and half the
     * height below it in portrait.
     */
    private void applySize() {
        final double w;
        final double h;
        if (full) {
            w = FULL_WIDTH;
            h = FULL_HEIGHT;
        } else if (isPortrait()) {
            w = FULL_WIDTH;
            h = HALF_HEIGHT;
        } else {
            w = HALF_WIDTH;
            h = FULL_HEIGHT;
        }
        resize(w, h);
    }

    @Override
    public void disposeImpl() {
        disposed = true;
        main.removeCallbacks(tick);
        overlay.dispose();
        io.shutdownNow();
    }

    @Override
    public void onDropDownSelectionRemoved() {
    }

    @Override
    public void onDropDownVisible(boolean visible) {
        // Stop polling while the pane is closed. The markers stay on the map;
        // there is just no point spending radio and battery refreshing a list
        // nobody is looking at.
        if (visible) {
            scheduleNext();
        } else {
            main.removeCallbacks(tick);
        }
    }

    @Override
    public void onDropDownSizeChanged(double width, double height) {
    }

    @Override
    public void onDropDownClose() {
        full = false;
        fullBtn.setText("Expand");
        main.removeCallbacks(tick);
    }

    // -- chrome ----------------------------------------------------------

    /** Show either the setup card or the working UI, never both. */
    private void syncChrome() {
        boolean paired = !book.isEmpty();
        setupCard.setVisibility(paired ? View.GONE : View.VISIBLE);
        serverRow.setVisibility(paired ? View.VISIBLE : View.GONE);
        actionsRow.setVisibility(paired ? View.VISIBLE : View.GONE);
        listView.setVisibility(paired ? View.VISIBLE : View.GONE);
        if (!paired) {
            emptyText.setVisibility(View.GONE);
            setStatus("Not connected to NetWatch yet.", R.color.nw_muted);
        }
        populateServerSpinner();
    }

    private void populateServerSpinner() {
        List<Server> all = book.all();
        List<String> labels = new ArrayList<>();
        for (Server s : all) {
            labels.add(s.display());
        }
        populatingServers = true;
        ArrayAdapter<String> a = new ArrayAdapter<>(pluginCtx,
                android.R.layout.simple_spinner_dropdown_item, labels);
        serverSpinner.setAdapter(a);
        int active = book.activeIndex();
        if (active >= 0 && active < labels.size()) {
            serverSpinner.setSelection(active);
        }
        populatingServers = false;
    }

    private void setStatus(String text, int colorRes) {
        statusText.setText(text);
        try {
            int c = pluginCtx.getResources().getColor(colorRes);
            statusText.setTextColor(c);
            statusDot.setTextColor(c);
        } catch (Throwable ignored) {
            // A missing colour resource must not take the pane down.
        }
    }

    private void toast(String msg) {
        Toast.makeText(uiCtx, msg, Toast.LENGTH_SHORT).show();
    }

    // -- data ------------------------------------------------------------

    /**
     * Read current positions, and trails when they are switched on.
     *
     * @param userAsked true when a button was pressed, which makes it worth
     *                  reporting "nothing changed" rather than staying silent
     */
    private void refresh(final boolean userAsked) {
        final Server server = book.active();
        if (server == null) {
            syncChrome();
            return;
        }
        if (userAsked) {
            setStatus("Contacting " + server.host + "…", R.color.nw_muted);
        }
        final boolean wantTracks = book.getBool(K_TRACKS, true);
        final String from = book.getString(K_FROM, "");
        final String to = book.getString(K_TO, "");

        io.execute(new Runnable() {
            public void run() {
                BridgeClient client = new BridgeClient(server);
                final BridgeClient.Result fixes = client.fixes();
                BridgeClient.Result trackResult = null;
                if (fixes.error == null && wantTracks) {
                    trackResult = client.tracks(from, to, TRACK_LIMIT);
                }
                final BridgeClient.Result trackFinal = trackResult;
                post(new Runnable() {
                    public void run() {
                        apply(fixes, trackFinal, userAsked, null);
                    }
                });
            }
        });
    }

    private void syncNow() {
        final Server server = book.active();
        if (server == null) {
            return;
        }
        setStatus("Asking Apple for new reports…", R.color.nw_muted);
        io.execute(new Runnable() {
            public void run() {
                final BridgeClient.Result r = new BridgeClient(server).sync();
                post(new Runnable() {
                    public void run() {
                        if (r.error != null) {
                            setStatus(r.error, R.color.nw_warn);
                            return;
                        }
                        String note = r.newReports > 0
                                ? ("Apple returned " + r.newReports + " new report"
                                   + (r.newReports == 1 ? "" : "s"))
                                : "No new reports from Apple yet";
                        // Reuse the fixes the sync already returned, then pull
                        // trails so a longer history shows up straight away.
                        apply(r, null, true, note);
                        if (book.getBool(K_TRACKS, true)) {
                            refresh(false);
                        }
                    }
                });
            }
        });
    }

    private void apply(BridgeClient.Result fixes, BridgeClient.Result trackResult,
                       boolean userAsked, String note) {
        if (disposed) {
            return;
        }
        if (fixes.error != null) {
            setStatus(fixes.error, fixes.unauthorised ? R.color.nw_bad : R.color.nw_warn);
            if (fixes.unauthorised) {
                // A wrong code is a setup problem, not a transient one: offer the
                // fix rather than leaving the operator to find it.
                offerRepair();
            }
            scheduleNext();
            return;
        }

        tags.clear();
        tags.addAll(fixes.tags);

        if (trackResult != null && trackResult.error == null) {
            for (Tag t : tags) {
                for (Tag src : trackResult.tags) {
                    if (t.uid.equals(src.uid) || (t.id >= 0 && t.id == src.id)) {
                        t.track.clear();
                        t.track.addAll(src.track);
                        break;
                    }
                }
            }
        }

        adapter.notifyDataSetChanged();
        emptyText.setVisibility(tags.isEmpty() ? View.VISIBLE : View.GONE);
        emptyText.setText("No tags in NetWatch yet. Add one in the desktop app.");

        overlay.sync(tags,
                book.getBool(K_MARKERS, true),
                book.getBool(K_TRACKS, true),
                book.getBool(K_RESPECT, true));

        pushCot();

        int withFix = 0;
        for (Tag t : tags) {
            if (t.hasFix()) {
                withFix++;
            }
        }
        StringBuilder sb = new StringBuilder();
        if (note != null) {
            sb.append(note).append("  ·  ");
        }
        sb.append(tags.size()).append(tags.size() == 1 ? " tag" : " tags");
        sb.append(", ").append(withFix).append(" located");
        if (!TextUtils.isEmpty(fixes.serverName)) {
            sb.append("  ·  ").append(fixes.serverName);
        }
        if (book.getBool(K_AUTO, false)) {
            sb.append("  ·  auto");
        }
        setStatus(sb.toString(), withFix > 0 ? R.color.nw_ok : R.color.nw_muted);
        if (userAsked && tags.isEmpty()) {
            toast("Connected, but NetWatch has no tags yet.");
        }
        scheduleNext();
    }

    private void pushCot() {
        final int mode = book.getInt(K_COT, COT_OFF);
        if (mode == COT_OFF || tags.isEmpty()) {
            return;
        }
        if (!CotPush.isAvailable()) {
            return;
        }
        final Server server = book.active();
        if (server == null) {
            return;
        }
        final boolean external = mode == COT_EXTERNAL;
        final String cotType = overlay.iconType();
        io.execute(new Runnable() {
            public void run() {
                BridgeClient.Result r = new BridgeClient.Result();
                List<String> events = new BridgeClient(server)
                        .cotEvents(COT_STALE_SECONDS, cotType, r);
                if (r.error != null) {
                    Log.w(TAG, "CoT fetch failed: " + r.error);
                    return;
                }
                final int sent = CotPush.send(events, external);
                Log.d(TAG, "dispatched " + sent + " CoT event(s), external=" + external);
            }
        });
    }

    private void scheduleNext() {
        main.removeCallbacks(tick);
        if (disposed || !book.getBool(K_AUTO, false) || !isVisible()) {
            return;
        }
        int idx = book.getInt(K_INTERVAL, 1);
        if (idx < 0 || idx >= INTERVAL_SECONDS.length) {
            idx = 1;
        }
        main.postDelayed(tick, INTERVAL_SECONDS[idx] * 1000L);
    }

    private void post(Runnable r) {
        if (!disposed) {
            main.post(r);
        }
    }

    // -- pairing ---------------------------------------------------------

    private void runDiscovery() {
        setStatus("Looking for NetWatch on this network…", R.color.nw_muted);
        io.execute(new Runnable() {
            public void run() {
                final List<Discovery.Found> found = Discovery.scan();
                post(new Runnable() {
                    public void run() {
                        if (found.isEmpty()) {
                            setStatus("No NetWatch found on this network.", R.color.nw_warn);
                            new AlertDialog.Builder(uiCtx)
                                    .setTitle("Nothing found")
                                    .setMessage("Check that NetWatch is running on your "
                                            + "computer and that its ATAK bridge is switched "
                                            + "on.\n\nIf this tablet reaches the computer over "
                                            + "a VPN such as Tailscale or WireGuard, discovery "
                                            + "cannot work — VPNs carry no broadcast "
                                            + "traffic. Paste the pairing link instead.")
                                    .setPositiveButton("Paste link",
                                            new android.content.DialogInterface.OnClickListener() {
                                                public void onClick(
                                                        android.content.DialogInterface d, int w) {
                                                    openPairDialog(null, true);
                                                }
                                            })
                                    .setNegativeButton("Close", null)
                                    .show();
                            return;
                        }
                        if (found.size() == 1) {
                            Server s = found.get(0).toServer();
                            setStatus("Found " + s.host + ". Enter the pair code.",
                                    R.color.nw_muted);
                            openPairDialog(s, false);
                            return;
                        }
                        final String[] labels = new String[found.size()];
                        for (int i = 0; i < found.size(); i++) {
                            Discovery.Found f = found.get(i);
                            labels[i] = (f.name.isEmpty() ? f.host : f.name)
                                    + "  (" + f.host + ")";
                        }
                        new AlertDialog.Builder(uiCtx)
                                .setTitle("Which computer?")
                                .setItems(labels,
                                        new android.content.DialogInterface.OnClickListener() {
                                            public void onClick(
                                                    android.content.DialogInterface d, int which) {
                                                openPairDialog(
                                                        found.get(which).toServer(), false);
                                            }
                                        })
                                .setNegativeButton("Cancel", null)
                                .show();
                    }
                });
            }
        });
    }

    /**
     * Pair with a computer.
     *
     * @param prefill a server discovered or being edited, or null for a blank form
     * @param linkFirst put the cursor in the pairing-link field
     */
    private void openPairDialog(final Server prefill, boolean linkFirst) {
        final View v = PluginLayoutInflater.inflate(pluginCtx, R.layout.dialog_pair, null);
        final EditText link = v.findViewById(R.id.pair_link);
        final EditText host = v.findViewById(R.id.pair_host);
        final EditText port = v.findViewById(R.id.pair_port);
        final EditText code = v.findViewById(R.id.pair_code);
        final EditText label = v.findViewById(R.id.pair_label);
        final TextView msg = v.findViewById(R.id.pair_msg);

        port.setText(String.valueOf(Server.DEFAULT_PORT));
        if (prefill != null) {
            host.setText(prefill.host);
            port.setText(String.valueOf(prefill.port));
            code.setText(prefill.code);
            label.setText(prefill.label);
        }
        if (linkFirst) {
            link.requestFocus();
        }

        v.findViewById(R.id.pair_apply_link).setOnClickListener(new View.OnClickListener() {
            public void onClick(View b) {
                Server s = Server.fromLink(link.getText().toString());
                if (s == null) {
                    msg.setText("That does not look like a NetWatch pairing link. It "
                            + "starts with netwatch://pair?");
                    msg.setVisibility(View.VISIBLE);
                    return;
                }
                host.setText(s.host);
                port.setText(String.valueOf(s.port));
                code.setText(s.code);
                if (!TextUtils.isEmpty(s.label)) {
                    label.setText(s.label);
                }
                msg.setText("Link read. Press Connect.");
                msg.setVisibility(View.VISIBLE);
            }
        });

        final AlertDialog dlg = new AlertDialog.Builder(uiCtx)
                .setTitle(R.string.setup_title)
                .setView(v)
                .setPositiveButton("Connect", null)   // set below, to keep it open on error
                .setNegativeButton("Cancel", null)
                .create();
        dlg.show();
        dlg.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(
                new View.OnClickListener() {
                    public void onClick(View b) {
                        Server s = Server.fromHostField(host.getText().toString(),
                                Server.parsePort(port.getText().toString()),
                                code.getText().toString());
                        if (s == null) {
                            msg.setText("Enter the computer's address.");
                            msg.setVisibility(View.VISIBLE);
                            return;
                        }
                        s.label = label.getText().toString().trim();
                        if (TextUtils.isEmpty(s.code)) {
                            msg.setText("Enter the pair code shown in NetWatch.");
                            msg.setVisibility(View.VISIBLE);
                            return;
                        }
                        testAndSave(s, dlg, msg);
                    }
                });
    }

    /**
     * Verify a server answers before saving it.
     *
     * <p>Saving first and failing later is how you get a customer staring at a
     * blank list with no idea which of the three fields is wrong.
     */
    private void testAndSave(final Server s, final AlertDialog dlg, final TextView msg) {
        msg.setText("Connecting to " + s.host + ":" + s.port + "…");
        msg.setVisibility(View.VISIBLE);
        io.execute(new Runnable() {
            public void run() {
                final BridgeClient.Result r = new BridgeClient(s).fixes();
                post(new Runnable() {
                    public void run() {
                        if (r.error != null) {
                            msg.setText(r.error);
                            return;
                        }
                        book.addOrUpdate(s);
                        dlg.dismiss();
                        syncChrome();
                        toast("Connected to " + (r.serverName.isEmpty()
                                ? s.host : r.serverName));
                        apply(r, null, true, null);
                    }
                });
            }
        });
    }

    /** Offered when the saved pair code stops working, usually after a rotate. */
    private void offerRepair() {
        final Server s = book.active();
        if (s == null) {
            return;
        }
        new AlertDialog.Builder(uiCtx)
                .setTitle("Pair code rejected")
                .setMessage("NetWatch on " + s.host + " did not accept the saved pair "
                        + "code. If it was changed in NetWatch, enter the new one.")
                .setPositiveButton("Enter code",
                        new android.content.DialogInterface.OnClickListener() {
                            public void onClick(android.content.DialogInterface d, int w) {
                                openPairDialog(s, false);
                            }
                        })
                .setNegativeButton("Later", null)
                .show();
    }

    private void openServerManager() {
        final List<Server> all = book.all();
        final String[] labels = new String[all.size()];
        for (int i = 0; i < all.size(); i++) {
            labels[i] = all.get(i).display();
        }
        new AlertDialog.Builder(uiCtx)
                .setTitle("NetWatch computers")
                .setItems(labels, new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface d, final int which) {
                        new AlertDialog.Builder(uiCtx)
                                .setTitle(all.get(which).display())
                                .setItems(new String[] { "Edit", "Remove" },
                                        new android.content.DialogInterface.OnClickListener() {
                                            public void onClick(
                                                    android.content.DialogInterface d2, int act) {
                                                if (act == 0) {
                                                    openPairDialog(all.get(which), false);
                                                } else {
                                                    book.remove(which);
                                                    overlay.clear();
                                                    tags.clear();
                                                    adapter.notifyDataSetChanged();
                                                    syncChrome();
                                                    if (book.active() != null) {
                                                        refresh(true);
                                                    }
                                                }
                                            }
                                        })
                                .show();
                    }
                })
                .setPositiveButton("Add another",
                        new android.content.DialogInterface.OnClickListener() {
                            public void onClick(android.content.DialogInterface d, int w) {
                                openPairDialog(null, true);
                            }
                        })
                .setNegativeButton("Close", null)
                .show();
    }

    // -- options ---------------------------------------------------------

    private void openOptions() {
        final View v = PluginLayoutInflater.inflate(pluginCtx, R.layout.dialog_options, null);
        final CheckBox markers = v.findViewById(R.id.opt_markers);
        final CheckBox trackBox = v.findViewById(R.id.opt_tracks);
        final CheckBox respect = v.findViewById(R.id.opt_respect);
        final CheckBox auto = v.findViewById(R.id.opt_auto);
        final Spinner icon = v.findViewById(R.id.opt_icon);
        final Spinner interval = v.findViewById(R.id.opt_interval);
        final Spinner cot = v.findViewById(R.id.opt_cot);
        final Button fromBtn = v.findViewById(R.id.opt_from);
        final Button toBtn = v.findViewById(R.id.opt_to);
        final TextView cotNote = v.findViewById(R.id.opt_cot_note);

        markers.setChecked(book.getBool(K_MARKERS, true));
        trackBox.setChecked(book.getBool(K_TRACKS, true));
        respect.setChecked(book.getBool(K_RESPECT, true));
        respect.setEnabled(trackBox.isChecked());
        auto.setChecked(book.getBool(K_AUTO, false));

        icon.setAdapter(new ArrayAdapter<>(pluginCtx,
                android.R.layout.simple_spinner_dropdown_item, TagOverlay.ICON_LABELS));
        icon.setSelection(clamp(book.getInt(K_ICON, 0), TagOverlay.ICON_LABELS.length));

        interval.setAdapter(new ArrayAdapter<>(pluginCtx,
                android.R.layout.simple_spinner_dropdown_item, INTERVAL_LABELS));
        interval.setSelection(clamp(book.getInt(K_INTERVAL, 1), INTERVAL_LABELS.length));
        interval.setEnabled(auto.isChecked());

        final String[] cotLabels = {
                pluginCtx.getString(R.string.cot_off),
                pluginCtx.getString(R.string.cot_local),
                pluginCtx.getString(R.string.cot_external),
        };
        cot.setAdapter(new ArrayAdapter<>(pluginCtx,
                android.R.layout.simple_spinner_dropdown_item, cotLabels));
        cot.setSelection(clamp(book.getInt(K_COT, COT_OFF), cotLabels.length));

        if (!CotPush.isAvailable()) {
            cot.setEnabled(false);
            cotNote.setText("Sharing is not available on this version of ATAK. Tags "
                    + "still appear on this device's map.");
        }

        trackBox.setOnCheckedChangeListener(
                new android.widget.CompoundButton.OnCheckedChangeListener() {
                    public void onCheckedChanged(
                            android.widget.CompoundButton b, boolean checked) {
                        respect.setEnabled(checked);
                    }
                });
        auto.setOnCheckedChangeListener(
                new android.widget.CompoundButton.OnCheckedChangeListener() {
                    public void onCheckedChanged(
                            android.widget.CompoundButton b, boolean checked) {
                        interval.setEnabled(checked);
                    }
                });

        final String[] window = { book.getString(K_FROM, ""), book.getString(K_TO, "") };
        fromBtn.setText("From: " + labelFor(window[0]));
        toBtn.setText("To: " + labelFor(window[1]));

        fromBtn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View b) {
                pickDate(window[0], new DateChosen() {
                    public void onDate(String iso) {
                        window[0] = iso;
                        fromBtn.setText("From: " + labelFor(iso));
                    }
                });
            }
        });
        toBtn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View b) {
                pickDate(window[1], new DateChosen() {
                    public void onDate(String iso) {
                        window[1] = iso;
                        toBtn.setText("To: " + labelFor(iso));
                    }
                });
            }
        });
        v.findViewById(R.id.opt_dates_clear).setOnClickListener(new View.OnClickListener() {
            public void onClick(View b) {
                window[0] = "";
                window[1] = "";
                fromBtn.setText("From: Any");
                toBtn.setText("To: Any");
            }
        });

        new AlertDialog.Builder(uiCtx)
                .setTitle("Options")
                .setView(v)
                .setPositiveButton("Save", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface d, int w) {
                        book.putBool(K_MARKERS, markers.isChecked());
                        book.putBool(K_TRACKS, trackBox.isChecked());
                        book.putBool(K_RESPECT, respect.isChecked());
                        book.putBool(K_AUTO, auto.isChecked());
                        book.putInt(K_ICON, icon.getSelectedItemPosition());
                        book.putInt(K_INTERVAL, interval.getSelectedItemPosition());
                        book.putInt(K_COT, cot.getSelectedItemPosition());

                        // An inverted range returns nothing and looks like a bug,
                        // so swap it rather than honouring the mistake.
                        String f = window[0];
                        String t = window[1];
                        if (!f.isEmpty() && !t.isEmpty() && f.compareTo(t) > 0) {
                            String swap = f;
                            f = t;
                            t = swap;
                            toast("Swapped the dates so the range runs forwards.");
                        }
                        book.putString(K_FROM, f);
                        book.putString(K_TO, t);

                        overlay.setIconIndex(icon.getSelectedItemPosition());
                        refresh(true);
                    }
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private static int clamp(int v, int len) {
        return (v < 0 || v >= len) ? 0 : v;
    }

    private static String labelFor(String iso) {
        return TextUtils.isEmpty(iso) ? "Any" : iso;
    }

    private interface DateChosen {
        void onDate(String iso);
    }

    private void pickDate(String current, final DateChosen cb) {
        Calendar cal = Calendar.getInstance();
        if (!TextUtils.isEmpty(current) && current.length() >= 10) {
            try {
                cal.set(Integer.parseInt(current.substring(0, 4)),
                        Integer.parseInt(current.substring(5, 7)) - 1,
                        Integer.parseInt(current.substring(8, 10)));
            } catch (Exception ignored) {
                // Fall back to today.
            }
        }
        new DatePickerDialog(uiCtx, new DatePickerDialog.OnDateSetListener() {
            public void onDateSet(android.widget.DatePicker view, int y, int m, int d) {
                cb.onDate(String.format(Locale.US, "%04d-%02d-%02d", y, m + 1, d));
            }
        }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH),
                cal.get(Calendar.DAY_OF_MONTH)).show();
    }

    // -- list ------------------------------------------------------------

    private class TagAdapter extends BaseAdapter {

        public int getCount() {
            return tags.size();
        }

        public Object getItem(int position) {
            return tags.get(position);
        }

        public long getItemId(int position) {
            return position;
        }

        public View getView(int position, View convertView, ViewGroup parent) {
            View row = convertView;
            if (row == null) {
                row = PluginLayoutInflater.inflate(pluginCtx, R.layout.tag_row, parent, false);
            }
            final Tag t = tags.get(position);
            ((TextView) row.findViewById(R.id.row_name)).setText(t.name);
            ((TextView) row.findViewById(R.id.row_sub)).setText(t.subtitle());
            row.findViewById(R.id.row_swatch).setBackgroundColor(t.argb());

            Button go = row.findViewById(R.id.row_goto);
            go.setEnabled(t.hasFix());
            go.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    overlay.panTo(t);
                }
            });
            row.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    if (t.hasFix()) {
                        overlay.panTo(t);
                    } else {
                        toast(t.name + " has no position yet. First fix can take "
                                + "5–40 minutes.");
                    }
                }
            });
            return row;
        }
    }
}
