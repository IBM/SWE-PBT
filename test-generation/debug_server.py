"""
Socket-based debug server for running inspectware inside a Docker container.
Listens for commands from the host and relays them to a persistent PDBTerminal session.
"""

import socket
import json
import sys
from inspectware import PDBTerminal

class DebugServer:
    def __init__(self, host='0.0.0.0', port=9999, target_file='/tmp/pbt_persistent/test.py'):
        self.host = host
        self.port = port
        self.target_file = target_file
        self.terminal = None
        self.socket = None
        
    def initialize_debugger(self):
        """Initialize the PDBTerminal instance."""
        print(f"[Server] Initializing debugger for {self.target_file}...")
        self.terminal = PDBTerminal(target_file=self.target_file)
        
        # Start debugging - detailed logging now happens in inspectware.py
        output = self.terminal.start_debugging()
        
        print(f"[Server] Debugger initialization complete")
        print(f"[Server] Session mode: {self.terminal.session}")
        print(f"[Server] State: {self.terminal.state}")
        
        # Verify PDB is ready
        if self.terminal.session != 'pdb':
            print(f"[Server] WARNING: Not in PDB mode (current: '{self.terminal.session}')")
            print(f"[Server] Check the PDBTerminal logs above for details on why")
        else:
            print(f"[Server] Successfully entered PDB mode")
        
        print(f"[Server] Debugger ready to accept commands")
        
    def start_server(self):
        """Start the socket server and listen for connections."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen(1)
        print(f"[Server] Listening on {self.host}:{self.port}")
        
    def handle_command(self, command_data):
        """Process a command and return the result."""
        try:
            command = command_data.get('command')
            
            if not command:
                return {
                    'success': False,
                    'error': 'No command provided'
                }, False
            
            print(f"[Server] Executing command: {command}")
            
            # Special handling for shutdown
            if command == 'shutdown':
                return {
                    'success': True,
                    'output': 'Server shutting down',
                    'session': self.terminal.session,
                    'state': self.terminal.state
                }, True
            
            # Execute command using terminal.exec_cmd
            output = self.terminal.exec_cmd(command)
            
            response = {
                'success': True,
                'output': output,
                'session': self.terminal.session,
                'state': self.terminal.state
            }
            
            return response, False
            
        except Exception as e:
            print(f"[Server] Error handling command: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }, False
    
    def run(self):
        """Main server loop."""
        try:
            self.initialize_debugger()
            self.start_server()
            
            while True:
                print("[Server] Waiting for connection...")
                conn, addr = self.socket.accept()
                print(f"[Server] Connected by {addr}")
                
                try:
                    while True:
                        # Receive data
                        data = conn.recv(32768)
                        if not data:
                            print("[Server] Client disconnected")
                            break
                        
                        # Parse JSON command
                        try:
                            command_data = json.loads(data.decode('utf-8'))
                            print(f"[Server] Received: {command_data}")
                        except json.JSONDecodeError as e:
                            response = {
                                'success': False,
                                'error': f'Invalid JSON: {e}'
                            }
                            conn.sendall(json.dumps(response).encode('utf-8'))
                            continue
                        
                        # Handle command
                        response, should_shutdown = self.handle_command(command_data)
                        
                        # Send response
                        response_json = json.dumps(response, indent=2)
                        conn.sendall(response_json.encode('utf-8'))
                        print(f"[Server] Sent response (success={response['success']})")
                        
                        if should_shutdown:
                            print("[Server] Shutdown requested")
                            conn.close()
                            return
                        
                except Exception as e:
                    print(f"[Server] Error in connection handler: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    conn.close()
                    
        except KeyboardInterrupt:
            print("\n[Server] Interrupted by user")
        except Exception as e:
            print(f"[Server] Fatal error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print("[Server] Cleaning up...")
            if self.socket:
                try:
                    self.socket.close()
                    print("[Server] Socket closed")
                except Exception as e:
                    print(f"[Server] Error closing socket: {e}")
            if self.terminal:
                try:
                    self.terminal.close()
                    print("[Server] Terminal closed")
                except Exception as e:
                    print(f"[Server] Error closing terminal: {e}")
            print("[Server] Server stopped")

def main():
    target_file = sys.argv[1] if len(sys.argv) > 1 else '/testbed/pbt/test.py'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
    
    server = DebugServer(host='0.0.0.0', port=port, target_file=target_file)
    server.run()

if __name__ == '__main__':
    main()
