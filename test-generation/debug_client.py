"""
Socket-based debug client for sending commands to the container debug server.
Connects to the container and sends inspectware-compatible commands.
"""

import socket
import json
import sys

class DebugClient:
    def __init__(self, host='localhost', port=9999):
        self.host = host
        self.port = port
        self.socket = None
        
    def connect(self):
        """Connect to the debug server."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((self.host, self.port))
            print(f"[Client] Connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"[Client] Failed to connect: {e}")
            return False
    
    def send_command(self, command, verbose=True):
        """Send a command to the debug server and receive response.
        
        Args:
            command: The PDB command to execute
            verbose: If True, print status messages. If False, silent operation.
            
        Returns:
            dict: Response with 'success', 'output', 'session', 'state' keys
        """
        raw_response = None
        try:
            # Prepare command payload
            payload = {'command': command}
            
            # Send command
            self.socket.sendall(json.dumps(payload).encode('utf-8'))
            if verbose:
                print(f"[Client] Sent command: {command}")
            
            # Receive response - handle potentially large responses
            chunks = []
            while True:
                chunk = self.socket.recv(32768)
                if not chunk:
                    break
                chunks.append(chunk)
                # Check if we have a complete JSON object
                try:
                    raw_response = b''.join(chunks).decode('utf-8')
                    json.loads(raw_response)  # Try to parse
                    break  # Success, we have complete JSON
                except json.JSONDecodeError:
                    # Not complete yet, continue receiving
                    continue
                except Exception:
                    # Other error, break and handle below
                    break
            
            if not chunks:
                if verbose:
                    print(f"[Client] Received empty response from server")
                return {'success': False, 'error': 'Empty response from server'}
            
            raw_response = b''.join(chunks).decode('utf-8')
            if verbose:
                print(f"[Client] Raw response ({len(raw_response)} bytes): {raw_response[:200]}...")
            
            response = json.loads(raw_response)
            
            return response
            
        except json.JSONDecodeError as e:
            error_msg = f"JSON decode error: {e}. Raw data: {raw_response[:500] if raw_response else 'None'}"
            if verbose:
                print(f"[Client] {error_msg}")
            return {'success': False, 'error': error_msg}
        except Exception as e:
            if verbose:
                print(f"[Client] Error sending command: {e}")
            return {'success': False, 'error': str(e)}
    
    def exec(self, command):
        """Execute a single command and return just the output string.
        
        This is a convenience method for programmatic use.
        
        Args:
            command: The PDB command to execute
            
        Returns:
            str: The command output, or error message if failed
            
        Example:
            client = DebugClient('localhost', 9999)
            client.connect()
            output = client.exec('break dbgtest.py:15')
            print(output)
            output = client.exec('continue')
            print(output)
            locals_dict = client.exec('p locals()')
            print(locals_dict)
        """
        response = self.send_command(command, verbose=False)
        if response.get('success'):
            return response.get('output', '')
        else:
            return f"Error: {response.get('error', 'Unknown error')}"
    
    def close(self):
        """Close the connection."""
        if self.socket:
            self.socket.close()
            print("[Client] Connection closed")
    
    def interactive_mode(self):
        """Run in interactive mode, accepting commands from user."""
        print("\n=== Interactive Debug Client ===")
        print("Enter PDB commands (or 'quit' to exit, 'help' for examples)")
        print("Examples:")
        print("  - break dbgtest.py:15")
        print("  - continue")
        print("  - next")
        print("  - step")
        print("  - where")
        print("  - p variable_name")
        print("  - !python_code")
        print("  - shutdown (to stop server)")
        print()
        
        while True:
            try:
                command = input("(pdb-remote) ").strip()
                
                if not command:
                    continue
                    
                if command.lower() in ['quit', 'exit', 'q']:
                    print("[Client] Exiting...")
                    break
                
                response = self.send_command(command)
                
                if response.get('success'):
                    print(f"\n--- Output ---")
                    print(response.get('output', ''))
                    print(f"\nSession: {response.get('session', 'unknown')}")
                    print(f"State: {response.get('state', 'unknown')}")
                    print()
                else:
                    print(f"\n[Error] {response.get('error', 'Unknown error')}")
                    if 'traceback' in response:
                        print(response['traceback'])
                    print()
                
                if command == 'shutdown':
                    print("[Client] Server shutdown requested")
                    break
                    
            except KeyboardInterrupt:
                print("\n[Client] Interrupted by user")
                break
            except Exception as e:
                print(f"[Client] Error: {e}")
                break
    
    def run_script(self, commands):
        """Run a list of commands in sequence."""
        print("\n=== Running Command Script ===")
        for i, command in enumerate(commands, 1):
            print(f"\n[{i}/{len(commands)}] Executing: {command}")
            response = self.send_command(command)
            
            if response.get('success'):
                print(f"--- Output ---")
                print(response.get('output', ''))
                print(f"Session: {response.get('session')}, State: {response.get('state')}")
            else:
                print(f"[Error] {response.get('error', 'Unknown error')}")
                if 'traceback' in response:
                    print(response['traceback'])
                break
