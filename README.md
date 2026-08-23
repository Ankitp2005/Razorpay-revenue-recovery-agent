# Razorpay Revenue Recovery Agent

AI agent that recovers revenue from failed Razorpay subscription auto-debits by classifying failure reasons, executing a decision engine, and simulating recovery outreach channels.

## Setup Instructions

1. Install dependencies:
   `ash
   pip install -r requirements.txt
   `

2. Generate the synthetic subscriptions dataset:
   `ash
   python generate_data.py
   `

3. Start the FastAPI recovery service:
   `ash
   uvicorn app.main:app --reload --port 8000
   `

4. In a separate terminal, replay the failed payment webhooks to test the agent:
   `ash
   python replay_batch.py
   `
