// PM2 entries for Ask The Ledger: the app server and its own Cloudflare tunnel
// (one tunnel per app). Crash-loop guards are mandatory; logs stay in ~/.pm2/logs.
// Start from this file; do not pm2 save without Ryan's say-so.
module.exports = {
  apps: [
    {
      name: "ask-the-ledger",
      script: "server.py",
      interpreter: "/opt/homebrew/bin/python3.11",
      cwd: "/Users/nobanksnearby/Documents/ask-the-ledger",
      env: { PORT: "17381" },
      autorestart: true,
      max_restarts: 5,
      min_uptime: 30000,
      exp_backoff_restart_delay: 2000,
      restart_delay: 5000,
      max_memory_restart: "200M",
    },
    {
      name: "ask-the-ledger-tunnel",
      script: "/opt/homebrew/bin/cloudflared",
      args: "tunnel --config /Users/nobanksnearby/.cloudflared/ask-the-ledger.yml run",
      interpreter: "none",
      cwd: "/Users/nobanksnearby/Documents/ask-the-ledger",
      autorestart: true,
      max_restarts: 5,
      min_uptime: 30000,
      exp_backoff_restart_delay: 2000,
      restart_delay: 5000,
      max_memory_restart: "150M",
    },
  ],
};
