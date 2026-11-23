# Community Console

Community Console is a self-hosted Windows dashboard and Discord bot. The dashboard uses Electron, setup uses Go, and the bot worker uses Python and discord.py.

## Install on Windows x64

1. Download `CommunityConsoleInstaller.exe` from the [latest release](https://github.com/Sammy-Avgeek/community-console/releases/latest) and run it.
2. Name your bot and choose a PNG or JPEG, or select the included image. Review the terms, select **I agree**, and click **Install**.
3. In setup, select **Open Developer Portal**. Use **Open in browser** if you prefer to sign in through your regular browser. Create a **New Application**, then enable **Server Members Intent** on its **Bot** page. Copy the bot token back into setup and select **Verify & connect**. Setup verifies the bot and saves its token and application ID locally. [Discord developer setup](https://docs.discord.com/developers/quick-start/getting-started)
4. Install the app on a server you manage using the `bot` and `applications.commands` scopes. In the dashboard, enter the server ID, welcome channel ID, private moderation log channel ID, and default member role ID. Save and run **Check server**, then **Start bot**.

The bot needs View Channel, Send Messages, and Embed Links where it posts. Grant Attach Files for bundled member banners, Manage Roles for role assignment, and Manage Channels plus Read Message History for tickets. Put the bot role above any roles it assigns.

## Dashboard

- **Bot** starts and stops the worker, shows activity, checks server permissions, and previews member messages.
- **Embeds** imports Discord or Discohook JSON and publishes completed drafts to writable channels.
- **Tickets** shows recent tickets and publishes or updates the support panel.
- **Settings** configures arrivals, departures, moderation, ticket routing, creator alerts, flight sim news, and Steam offers.
- **Settings → Branding** lets you choose a display name and PNG or JPEG logo. Apply them to the Discord bot if desired.

Keep the dashboard open while the bot is running. Configuration, the bot token, and the SQLite database are stored locally under `%APPDATA%\Community Console`. Previous installations reuse their existing data folder.

## Build from source

Install 64-bit Python 3.11 or later, Node.js 22.12 or later, and Go 1.23 or later on Windows. Open PowerShell in this folder.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe .\build_windows.py
```

The build downloads the pinned Electron version, packages the Discord worker, and bundles the runtime and dashboard files into the Go installer. The build verifies that the standalone installer can assemble the app in a temporary directory. `CommunityConsoleInstaller.exe` is written to `build`.

See `setup/TERMS.txt` for the installer terms and `THIRD-PARTY-NOTICES.txt` for bundled dependency notices.
