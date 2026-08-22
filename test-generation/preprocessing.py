from tree_sitter import Language, Parser
import tree_sitter_python as tspython
import re
import os
import json


def get_files(repo_path):
    """
    Get all the Python files in the repo
    """
    python_files = []
    for root, dirs, files in os.walk(repo_path):
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))

    return python_files
    

class PythonPreProcessing:
    """A tokenizer for Python code using tree-sitter."""
    
    def __init__(self):
        """Initialize the Python tokenizer with tree-sitter parser."""
        # Create parser and set language
        self.parser = Parser()
        self.language = Language(tspython.language())
        self.parser.language = self.language

  
    
    def tokenize(self, source_code):
        """
        Tokenize Python source code.
        
        Args:
            source_code (str): Python source code to tokenize
            
        Returns:
            list: List of token dictionaries with type, text, start, and end positions
        """
        # Parse the source code
        tree = self.parser.parse(bytes(source_code, "utf8"))
        
        # Extract tokens from the syntax tree
        tokens = []
        self._traverse_tree(tree.root_node, source_code, tokens)
        
        return tokens
    
    def _traverse_tree(self, node, source_code, tokens):
        """
        Recursively traverse the syntax tree and extract tokens.
        
        Args:
            node: Current tree-sitter node
            source_code (str): Original source code
            tokens (list): List to append tokens to
        """
        # If node has no children, it's a leaf node (token)
        
        if len(node.children) == 0:
            token_text = source_code[node.start_byte:node.end_byte]
            
            # Skip empty tokens
            if token_text.strip():
                tokens.append({
                    'type': node.type,
                    'text': token_text,
                    'start_line': node.start_point[0] + 1,
                    'start_column': node.start_point[1] + 1,
                    'end_line': node.end_point[0] + 1,
                    'end_column': node.end_point[1] + 1,
                    'start_byte': node.start_byte,
                    'end_byte': node.end_byte
                })
        else:
            # Recursively process children
            for child in node.children:
                self._traverse_tree(child, source_code, tokens)

    def print_tokens(self, tokens):
        """
        Pretty print tokens in a formatted table.
        
        Args:
            tokens (list): List of token dictionaries
        """
        print(f"\n{'Type':<25} {'Text':<35} {'Position':<20}")
        print("-" * 80)
        
        for token in tokens:
            position = f"L{token['start_line']}:C{token['start_column']}"
            text = token['text'][:32] + "..." if len(token['text']) > 35 else token['text']
            print(f"{token['type']:<25} {text:<35} {position:<20}")
    
    def get_class_related_data(self, python_file):
        """
        Extract imports, class name, attributes, methods, method bodies, and caller-callee relationships from a Python file.
        
        Args:
            python_file: Path to the Python file
            
        Returns:
            Tuple of (data, method_bodies) where:
            - data: Dictionary containing:
                - imports: List of import statements
                - class_name: Primary class name (or list if multiple)
                - attributes: List of class-level attribute assignments
                - methods: List of method/function names
                - caller_callee: Dictionary mapping each method to the methods it calls
            - method_bodies: Dictionary mapping each method name to its complete body
        """
        data = {
            'imports': [],
            'class_name': [],
            'attributes': [],
            'methods': [],
            'caller_callee': {}
        }
        
        method_bodies = {}
        
        try:
            # Read the Python file
            with open(python_file, 'r', encoding='utf-8', errors='ignore') as f:
                source_code = f.read()

            # Parse the source code
            tree = self.parser.parse(bytes(source_code, "utf8"))
            root_node = tree.root_node
            
            # Helper function to get node text
            def get_node_text(node):
                return source_code[node.start_byte:node.end_byte]
            
            # Helper function to traverse tree and find nodes by type
            def find_nodes_by_type(node, node_type):
                results = []
                if node.type == node_type:
                    results.append(node)
                for child in node.children:
                    results.extend(find_nodes_by_type(child, node_type))
                return results
            
            # Extract imports (both import and from...import statements)
            import_nodes = find_nodes_by_type(root_node, 'import_statement')
            import_from_nodes = find_nodes_by_type(root_node, 'import_from_statement')
            
            for import_node in import_nodes + import_from_nodes:
                import_text = get_node_text(import_node).strip()
                data['imports'].append(import_text)
            
            # Extract class names
            class_nodes = find_nodes_by_type(root_node, 'class_definition')
            for class_node in class_nodes:
                # Find the identifier (class name)
                for child in class_node.children:
                    if child.type == 'identifier':
                        class_name = get_node_text(child)
                        if class_name not in data['class_name']:
                            data['class_name'].append(class_name)
                        break
            
            # Extract attributes (assignments at class level)
            for class_node in class_nodes:
                # Get the class body
                for child in class_node.children:
                    if child.type == 'block':
                        # Look for assignment statements in the class body
                        for statement in child.children:
                            if statement.type == 'expression_statement':
                                for expr_child in statement.children:
                                    if expr_child.type == 'assignment':
                                        attr_text = get_node_text(expr_child).strip()
                                        # Clean up the attribute text
                                        attr_text = ' '.join(attr_text.split())
                                        if attr_text and attr_text not in data['attributes']:
                                            data['attributes'].append(attr_text)
            
            # Extract methods/functions, method bodies, and caller-callee relationships
            function_nodes = find_nodes_by_type(root_node, 'function_definition')
            
            for func_node in function_nodes:
                # Get function/method name
                func_name = ''
                for child in func_node.children:
                    if child.type == 'identifier':
                        # Extract only the identifier text, not parameters
                        func_name = get_node_text(child).strip()
                        # Remove any trailing parentheses or parameters if present
                        if '(' in func_name:
                            func_name = func_name.split('(')[0].strip()
                        break
                
                if func_name and func_name not in data['methods']:
                    data['methods'].append(func_name)
                
                # Extract the complete function body
                if func_name:
                    func_body = get_node_text(func_node).strip()
                    method_bodies[func_name] = func_body
                
                # Extract function calls within this function (caller-callee relationships)
                if func_name:
                    called_functions = []
                    call_nodes = find_nodes_by_type(func_node, 'call')
                    
                    for call_node in call_nodes:
                        # Get the function being called
                        # A call node structure: function_name(arguments)
                        # We only want the function name, not the arguments
                        called_func = None
                        
                        for child in call_node.children:
                            if child.type == 'identifier':
                                # Direct function call: func_name()
                                called_func = get_node_text(child).strip()
                                # Remove any parentheses or parameters
                                if '(' in called_func:
                                    called_func = called_func.split('(')[0].strip()
                                break
                            elif child.type == 'attribute':
                                # Method call: obj.method()
                                # Extract only the method name (last part after the dot)
                                attr_text = get_node_text(child).strip()
                                # Remove any parentheses or parameters first
                                if '(' in attr_text:
                                    attr_text = attr_text.split('(')[0].strip()
                                # Get the last identifier after the last dot
                                if '.' in attr_text:
                                    called_func = attr_text.split('.')[-1].strip()
                                else:
                                    called_func = attr_text
                                break
                        
                        # Add the function if it's valid and not already in the list
                        if called_func and called_func not in called_functions:
                            called_functions.append(called_func)
                    
                    # Store the caller-callee relationship
                    if called_functions:
                        data['caller_callee'][func_name] = called_functions
            
            # Convert class_name list to string if only one class, keep as list if multiple
            if len(data['class_name']) == 1:
                data['class_name'] = data['class_name'][0]
            elif len(data['class_name']) == 0:
                data['class_name'] = ''
                
        except Exception as e:
            # Log error but return empty structure
            print(f"Error parsing {python_file}: {str(e)}")
        
        return data, method_bodies


def preprocessing_instance(instance):

    name = instance['repo'].split("/")[-1]
    instance_id = instance['instance_id']
    print("Instance_id: " + instance_id)

    # point to the repo
    path = "../../repo/" + instance_id + "/" + name

    python_files = get_files(path)
    class_summary = {}
    method_body = {}

    for item in python_files:
        print("Processing: " + item)

        ppp = PythonPreProcessing()
        # this function will return imports, class name, method name, attribute name 
        data, method_bodies = ppp.get_class_related_data(item)

        class_summary[item.replace(path + "/", "")] = data
        method_body[item.replace(path + "/", "")] = method_bodies

    with open("preprocessed_data/" + instance_id + "/" + "class_info.json", "w") as f:
        json.dump(class_summary, f, indent=4)

    with open("preprocessed_data/" + instance_id + "/" + "method_bodies.json", "w") as f:
        json.dump(method_body, f, indent=4)       



# Made with Bob