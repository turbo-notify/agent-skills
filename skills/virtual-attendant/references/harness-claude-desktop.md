# Claude Desktop and Cowork

Claude Desktop and Cowork run the attendant **in session, in assisted mode**. They cannot run while the app is closed and cannot give each customer a context of their own, so a person approves every draft. This is the easiest start for someone who does not use a terminal.

## Install and keep it current

Add the marketplace `turbo-notify/agent-skills` as a plugin source and install the plugin `turbo-notify`:

- In a Team or Enterprise plan, an Owner adds the repository in the organization settings (Plugins and skills) and turns on automatic sync, so everyone stays current.
- On your own account, in Plugins, add the marketplace by its address and use **Check for updates** from time to time.
- A plugin uploaded by hand as a file never updates. Install it from the marketplace instead.

Each cycle also checks the version and tells the administrators when a new one exists.

## Connect Turbo Notify

Add the connector with the address `https://mcp.turbonotify.com` and sign in with your Turbo Notify account (or use an API key header, as the Turbo Notify guide "MCP server" explains).

## Needs a folder

The workspace is a folder on this computer. Use Cowork with a folder you choose (for example `~/.turbo-notify/attendant`). A plain chat with no access to files cannot keep the attendant's memory, and the attendant will not run there. If Python is not available to Cowork, you are in manual mode ([setup.md](setup.md), "Without Python"): say so and use assisted mode only.

## Run

Tell it "answer the pending customers". It runs one cycle ([tick.md](tick.md)), shows the drafts, and the administrators and attendants approve with `/enviar`. Say this plainly to the person: **nothing is answered while the app is closed**. For answers around the clock use the Claude Code or Codex runner.
