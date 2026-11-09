# Install the Turbo Notify MCP server in Cline

Turbo Notify is a hosted MCP server. There is nothing to download, build or run on your machine: Cline connects to `https://mcp.turbonotify.com` over Streamable HTTP.

## 1. Add the server

Add the entry below under `mcpServers` in Cline's MCP settings file, `cline_mcp_settings.json`. Create the file if it does not exist yet, and keep any servers that are already in it. Do not use `cline mcp add`: it asks questions in an interactive terminal.

- Cline in VS Code, JetBrains or Cursor: the MCP Servers icon in the Cline panel, then the Configure tab, then Configure MCP Servers, opens the file.
- Cline CLI: `~/.cline/data/settings/cline_mcp_settings.json`, or the path in the `CLINE_MCP_SETTINGS_PATH` environment variable when it is set.

```json
{
  "mcpServers": {
    "turbo-notify": {
      "type": "streamableHttp",
      "url": "https://mcp.turbonotify.com",
      "disabled": false
    }
  }
}
```

## 2. Sign in

Turbo Notify signs people in with OAuth. If Cline offers to authorize `turbo-notify`, open the authorization: the browser goes to the Turbo Notify sign-in, where the person creates an account or signs in and links their WhatsApp number. Do not ask for a password in the chat.

If the person prefers an API key, they create one in the Turbo Notify dashboard (API keys) and you add a header to the same entry. Ask them for the key; never invent one.

```json
"headers": { "Authorization": "Bearer THEIR_API_KEY" }
```

## 3. Check that it works

The server lists 36 tools, such as `list_numbers`, `send_text` and `list_messages`. Call `list_numbers` first: it shows the numbers the account can send from. Sending goes out from the person's own WhatsApp number, so confirm with them before you send.

Documentation: https://docs.turbonotify.com/en/agents/mcp-server/
