"""
Utility module for LLM API calls and related helper functions.
Contains centralized functions for interacting with Claude and GPT models.
"""

import os
import openai
from difflib import get_close_matches

def get_llm_client():
    """
    Initialize and return the OpenAI client configured for LiteLLM proxy.
    
    Returns:
        openai.OpenAI: Configured OpenAI client instance
    """
    client = openai.OpenAI(
        api_key=os.getenv("CLAUDE_API"),
        base_url="https://ete-litellm.bx.cloud9.ibm.com"
    )
    return client

def generate_text(prompt, model="claude", temperature=0, max_tokens=8192):
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
    client = get_llm_client()
    
    if model == "claude":
        response = client.chat.completions.create(
            model="aws/claude-sonnet-4-5",
            # model="claude-sonnet-4-6",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    
    elif model == "gpt":
        response = client.chat.completions.create(
            model="Azure/gpt-5.2-2025-12-11",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    
    else:
        raise ValueError(f"Unsupported model: {model}. Use 'claude' or 'gpt'.")

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

