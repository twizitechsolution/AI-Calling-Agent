# Enterprise Deployment Guide

This guide covers deploying your Twizitech AI Voice Agent for high-reliability, low-latency, and high-concurrency production environments using Twilio, Docker, and Supabase.

## 1. SIP Trunk Configuration (Vobiz / Twilio)
To handle concurrent outbound and inbound calls without rate limits or line busy errors, you need a SIP provider that supports **multiple concurrent channels**.

**Using Vobiz (Your Current Setup)**
Vobiz is perfectly capable of handling enterprise voice agents for the Indian market! 
- Simply ensure that your Vobiz plan allows for **multiple concurrent SIP channels**. If your plan only allows 1 simultaneous call, the second incoming/outgoing call will fail.
- You can continue using your exact setup! Run `python setup_trunk.py` with your `VOBIZ_SIP_DOMAIN` and `VOBIZ_USERNAME` credentials in `.env` to securely bind LiveKit.

**Using alternatives like Twilio/Plivo:**
If you ever expand outside India or want alternative infrastructure, providers like Twilio or Plivo are also options, configured using the same LiveKit update scripts.

## 2. Supabase Cloud Setup
You must apply the analytics migration to activate enterprise analytics on the dashboard:
1. Open Supabase SQL Editor.
2. Run the code from `supabase_migration_v2.sql`.
3. This creates fields for `sentiment`, `estimated_cost_usd`, and `was_booked`, which power the AI Voice Dashboard.

## 3. Deployment via Docker (AWS EC2 / Coolify)
1. Do not use `python agent.py` in a Linux screen/tmux session for enterprise deployment.
2. Push your repository to GitHub.
3. Deploy via **Coolify** or using Docker Compose on **AWS EC2/DigitalOcean**:
   ```bash
   docker build -t twizitech-voice-agent .
   docker run -d --name voice-agent --env-file .env -p 8000:8000 twizitech-voice-agent
   ```
4. The multi-stage `Dockerfile` uses `supervisord` to automatically keep BOTH the FastAPI (`ui_server.py`) and the Agent Core (`agent.py`) running persistently.

## 4. API Throttling Checks
Ensure you have switched to paid tiers for:
- **Groq/OpenAI**: Paid APIs remove the 30-rpm limit preventing "slow audio" buffer flushes.
- **Sarvam AI**: Ensure you are not on the free tier to prevent synthetic voice throttling.
- **LiveKit Cloud**: Upgrade from the free Hobby tier if expecting >50 concurrent calls.
