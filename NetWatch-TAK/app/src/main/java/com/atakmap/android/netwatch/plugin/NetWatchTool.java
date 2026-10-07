
package com.atakmap.android.netwatch.plugin;

import android.content.Context;

import com.atak.plugins.impl.AbstractPluginTool;
import com.atakmap.android.netwatch.NetWatchDropDownReceiver;

import gov.tak.api.util.Disposable;

/**
 * The toolbar button that opens the NetWatch pane.
 */
public class NetWatchTool extends AbstractPluginTool implements Disposable {

    public NetWatchTool(Context context) {
        super(context,
                context.getString(R.string.app_name),
                context.getString(R.string.app_desc),
                context.getResources().getDrawable(R.drawable.ic_netwatch),
                NetWatchDropDownReceiver.SHOW_PLUGIN);
    }

    @Override
    public void dispose() {
    }
}
