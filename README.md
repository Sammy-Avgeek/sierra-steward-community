# Sierra Steward Community Edition

Sierra Steward is a self-hosted Windows dashboard and Discord bot. The dashboard uses Electron, setup uses Go, and the bot worker uses Python and discord.py.

## Install on Windows x64

1. Download `SierraStewardCommunityInstaller.exe` from the [latest release](https://github.com/Sammy-Avgeek/sierra-steward-community/releases/latest) and run it.
2. Review the terms, select **I agree**, and click **Install**. Setup builds the runnable Electron app from its bundled components. Open the dashboard when setup finishes.
3. Create your own Discord application and install it on a server you manage. Use the `bot` and `applications.commands` scopes and enable **Server Members Intent**. [Discord developer setup](https://docs.discord.com/developers/quick-start/getting-started)
4. In the dashboard, enter the bot token, application ID, server ID, welcome channel ID, private moderation log channel ID, and default member role ID. Save and run **Check server**, then **Start bot**.

The bot needs View Channel, Send Messages, and Embed Links where it posts. Grant Attach Files for bundled member banners, Manage Roles for role assignment, and Manage Channels plus Read Message History for tickets. Put the bot role above any roles it assigns.

## Dashboard

- **Bot** starts and stops the worker, shows activity, checks server permissions, and previews member messages.
- **Embeds** imports Discord or Discohook JSON and publishes completed drafts to writable channels.
- **Tickets** shows recent tickets and publishes or updates the support panel.
- **Settings** configures arrivals, departures, moderation, ticket routing, creator alerts, flight sim news, and Steam offers.
- **Settings → Branding** lets you choose a display name and PNG or JPEG logo. Apply them to the Discord bot if desired.

Keep the dashboard open while the bot is running. Configuration, the bot token, and the SQLite database are stored locally under `%APPDATA%\Sierra Steward Community`.

## Build from source

Install 64-bit Python 3.11 or later, Node.js 22.12 or later, and Go 1.23 or later on Windows. Open PowerShell in this folder.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe .\build_windows.py
```

The build downloads the pinned Electron version, packages the Discord worker, and bundles the runtime and dashboard files into the Go installer. The build verifies that the standalone installer can assemble the app in a temporary directory. `SierraStewardCommunityInstaller.exe` is written to `build`.

See `setup/TERMS.txt` for the installer terms and `THIRD-PARTY-NOTICES.txt` for bundled dependency notices.
