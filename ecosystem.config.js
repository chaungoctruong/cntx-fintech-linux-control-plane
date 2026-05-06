/**
 * PM2 Standard Configuration
 * Quản lý tập trung Backend (API + Frontend) và Hubbot (Telegram)
 */
module.exports = {
  apps: [
    {
      name: "spider-backend",
      cwd: "/root/spider-ai/backend_ai/backend",
      script: "/root/spider-ai/backend_ai/backend/venv/bin/python3",
      args: "scripts/run_api.py",
      interpreter: "none",
      exec_mode: "fork",
      instances: 2,
      instance_var: "INSTANCE_ID",
      env: { 
        PYTHONPATH: ".", 
        CNTX_ROLE: "api",
        API_PORT_BASE: "8002",
        SERVICE_MODE: "local",
      },
      autorestart: true,
      kill_timeout: 15000,
      max_memory_restart: "1500M",
      error_file: "/root/spider-ai/logs/backend-err.log",
      out_file: "/root/spider-ai/logs/backend-out.log",
    },
    {
      name: "spider-hubbot",
      cwd: "/root/spider-ai/hubbot",
      script: "/root/spider-ai/hubbot/venv_hub/bin/python3",
      args: "main.py",
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_memory_restart: "500M",
      error_file: "/root/spider-ai/logs/hubbot-err.log",
      out_file: "/root/spider-ai/logs/hubbot-out.log",
    }
  ],
};
