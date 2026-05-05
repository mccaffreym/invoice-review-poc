import os
from dotenv import load_dotenv
from anthropic import Anthropic

# Load API key from .env file
load_dotenv()

# Create the client (it picks up ANTHROPIC_API_KEY from the environment)
client = Anthropic()

# Send a message to Claude
response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=200,
    messages=[
        {"role": "user", "content": "In two sentences, what is block billing in legal invoices?"}
    ]
)

# Print Claude's reply
print(response.content[0].text)