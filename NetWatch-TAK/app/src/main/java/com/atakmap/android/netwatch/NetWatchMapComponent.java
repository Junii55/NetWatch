
package com.atakmap.android.netwatch;

import android.content.Context;
import android.content.Intent;

import com.atakmap.android.dropdown.DropDownMapComponent;
import com.atakmap.android.ipc.AtakBroadcast.DocumentedIntentFilter;
import com.atakmap.android.maps.MapView;
import com.atakmap.android.netwatch.plugin.R;
import com.atakmap.coremap.log.Log;

public class NetWatchMapComponent extends DropDownMapComponent {

    private static final String TAG = "NetWatchMapComponent";
    private NetWatchDropDownReceiver receiver;

    @Override
    public void onCreate(final Context context, Intent intent, final MapView view) {
        context.setTheme(R.style.ATAKPluginTheme);
        super.onCreate(context, intent, view);
        try {
            receiver = new NetWatchDropDownReceiver(view, context);
            DocumentedIntentFilter filter = new DocumentedIntentFilter();
            filter.addAction(NetWatchDropDownReceiver.SHOW_PLUGIN);
            registerDropDownReceiver(receiver, filter);
        } catch (Throwable t) {
            Log.e(TAG, "NetWatch failed to start", t);
        }
    }

    @Override
    protected void onDestroyImpl(Context context, MapView view) {
        if (receiver != null) {
            receiver.dispose();
            receiver = null;
        }
        super.onDestroyImpl(context, view);
    }
}
