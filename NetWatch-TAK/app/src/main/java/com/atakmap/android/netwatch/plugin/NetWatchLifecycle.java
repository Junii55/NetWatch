/*
 * NetWatch-TAK — NetWatch tags on the ATAK map.
 *
 * Copyright (C) 2026 junii55
 *
 * This program is free software: you can redistribute it and/or modify it under
 * the terms of the GNU Affero General Public License as published by the Free
 * Software Foundation, either version 3 of the License, or (at your option) any
 * later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
 * details.
 *
 * You should have received a copy of the GNU Affero General Public License along
 * with this program. If not, see <https://www.gnu.org/licenses/>.
 *
 * Selling this plugin, or distributing it inside a product, requires a separate
 * commercial licence — see COMMERCIAL-LICENSE.md.
 */
package com.atakmap.android.netwatch.plugin;

import com.atak.plugins.impl.AbstractPlugin;
import com.atak.plugins.impl.PluginContextProvider;
import com.atakmap.android.netwatch.NetWatchMapComponent;

import gov.tak.api.plugin.IServiceController;

/**
 * Plugin entry point, named in assets/plugin.xml.
 */
public class NetWatchLifecycle extends AbstractPlugin {

    public NetWatchLifecycle(IServiceController serviceController) {
        super(serviceController,
                new NetWatchTool(serviceController
                        .getService(PluginContextProvider.class).getPluginContext()),
                new NetWatchMapComponent());
    }
}
