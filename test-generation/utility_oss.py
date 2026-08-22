"""
Utility module for LLM API calls and related helper functions.
Contains centralized functions for interacting with Claude and GPT models.
"""

import os
import openai
from difflib import get_close_matches

import requests

# def get_llm_client():
#     """
#     Initialize and return the OpenAI client configured for LiteLLM proxy.
    
#     Returns:
#         openai.OpenAI: Configured OpenAI client instance
#     """
#     client = openai.OpenAI(
#         api_key=os.getenv("CLAUDE_API"),
#         base_url="https://ete-litellm.bx.cloud9.ibm.com"
#     )
#     return client

def get_llm_client() -> tuple[str, str, dict[str, str]]:
    
    # Get configuration from environment variables
    endpoint = os.getenv('RITS_ENDPOINT', 'https://inference-3scale-apicast-production.apps.rits.fmaas.res.ibm.com/gpt-oss-120b/v1/chat/completions')
    model_name = os.getenv('RITS_MODEL_NAME', 'openai/gpt-oss-120b')
    api_key = os.getenv('RITS_API_KEY', 'your-api-key-here')
    # max_turns = int(os.getenv('MAX_TURNS', '100')) # original otter seems to be single-turn

    headers = {}

    if api_key == 'your-api-key-here':
        print("\n⚠️  Set environment variables:")
        print("  export RITS_ENDPOINT='https://your-endpoint.com/v1/chat/completions'")
        print("  export RITS_MODEL_NAME='your-model-name'")
        print("  export RITS_API_KEY='your-api-key'")
    else:
        headers = {
            'RITS_API_KEY': api_key,
            'Content-Type': 'application/json'
        }

    return endpoint, model_name, headers

def generate_text(prompt, model="oss", temperature=0, max_tokens=8192):
    """
    Generate text using the specified LLM model.
    
    Args:
        prompt (str): The prompt to send to the model
        model (str): Model to use - "claude" or "gpt" (default: "claude")
        temperature (float): Temperature for generation (default: 0)
        max_tokens (int): Maximum tokens to generate (default: 8192)
    
    Returns:
        str: Generated text from the model
    """

    messages = [({"role": "user", "content": prompt})]
    endpoint, model_name, headers = get_llm_client()
    
    # Prepare request
    data = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,  # Increased to allow for reasoning + content
        "temperature": 0.7,
        "include_reasoning": False  # Disable reasoning to get content
    }

    try:
        # Make request to RITS
        response = requests.post(endpoint, headers=headers, json=data)
        response.raise_for_status()
        
        # Parse response
        result = response.json()

        if result.get('choices'):
            choice = result['choices'][0]
            message = choice.get('message', {})
            assistant_message = message.get('content', '')
            
            # Debug: print if content is None or empty
            if not assistant_message:
                print(f"DEBUG - Full response: {result}\n")
                assistant_message = "[No content in response]"
            
            # # Add assistant response to history
            # messages.append({"role": "assistant", "content": assistant_message})
            # # Display response
            # print(f"Assistant: {assistant_message}\n")
            
            # Show token usage
            if 'usage' in result:
                tokens = result['usage'].get('total_tokens', 'N/A')
                print(f"[Tokens used: {tokens}]\n")

            return assistant_message
        else:
            msg = "Error: No response from server\n"
            return msg

    except requests.exceptions.RequestException as e:
        e_str= f"Error: {e}\n"
        print(e_str)
        
        return e_str

def find_min_edit_distance(query, candidates):
    """
    Find the candidate string with minimum edit distance to the query.
    
    Args:
        query (str): The query string to match
        candidates (list): List of candidate strings to compare against
    
    Returns:
        str: The candidate string with best match to query
    """
    matches = get_close_matches(query, candidates, n=1, cutoff=0.0)
    return matches[0] if matches else ""

