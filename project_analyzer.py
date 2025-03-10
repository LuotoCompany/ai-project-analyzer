import os
import argparse
from pathlib import Path
from typing import List, Dict
from openai import AzureOpenAI
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
import json
from collections import defaultdict
import fnmatch
import sys

# Load environment variables
load_dotenv()

# Set up Azure OpenAI client
credential = DefaultAzureCredential()
AZURE_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
azure_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), AZURE_COGNITIVE_SERVICES_SCOPE
)

llm = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_API_BASE_URL"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_ad_token_provider=azure_token_provider,
)

# Directories to ignore
IGNORE_DIRS = {
    'node_modules', 'build', 'dist', '__pycache__', 'venv', 'env', '.git', 
    '.github', '.vscode', '.idea', 'target', 'bin', 'obj', 'out', 'coverage'
}


class ProjectAnalyzer:
    def __init__(self, base_path: str, include: str, batch_size: int = 10):
        self.base_path = Path(base_path).resolve()
        self.batch_size = batch_size
        self.include_patterns = [pattern.strip() for pattern in include.split(',')]
        self.all_files = []
        self.processed_files = set()
        self.dependency_graph = "graph TD\n"
        self.file_descriptions = {}
        # Map folders to descriptions
        self.module_descriptions = defaultdict(str)
        self.project_description = ""
        self.features = set()  # Collection of project features
        # Collection of data models and their relationships
        self.data_models = {}

    def should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        # Ignore hidden files and directories
        if path.name.startswith('.'):
            return True

        # Ignore specific directories
        if path.is_dir() and path.name in IGNORE_DIRS:
            return True

        # For files, check if they match any of the include patterns
        if path.is_file():
            # Check if the file matches any of the include patterns
            for pattern in self.include_patterns:
                if fnmatch.fnmatch(path.name, pattern):
                    return False
            # If no patterns match, ignore the file
            return True

        return False

    def collect_files(self) -> List[Path]:
        """Collect all files in the project that match the include patterns."""
        self.all_files = []
        
        for root, dirs, files in os.walk(self.base_path):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if not self.should_ignore(Path(root) / d)]

            for file in files:
                file_path = Path(root) / file
                if not self.should_ignore(file_path):
                    rel_path = file_path.relative_to(self.base_path)
                    self.all_files.append(rel_path)

        print("Files to be processed:")
        for file in sorted(self.all_files):
            print(str(file))
        print(f"Found {len(self.all_files)} files to analyze.")
        
        # Ask for user confirmation before proceeding
        while True:
            file_count = len(self.all_files)
            prompt = "Do you want to proceed with the analysis of "
            prompt += f"{file_count} files? (y/n): "
            response = input(prompt)
            if response.lower() == 'y':
                break
            elif response.lower() == 'n':
                print("Analysis cancelled by user.")
                sys.exit(0)
            else:
                print("Please enter 'y' or 'n'.")
        
        return self.all_files

    def create_batches(self) -> List[List[Path]]:
        """Create logical batches of files for processing."""
        # Group files by directory first
        dir_groups = {}
        for file in self.all_files:
            if file in self.processed_files:
                continue

            parent = str(file.parent)
            if parent not in dir_groups:
                dir_groups[parent] = []
            dir_groups[parent].append(file)

        # Create batches prioritizing files from the same directory
        batches = []
        current_batch = []

        # First try to keep directories together
        for dir_path, files in dir_groups.items():
            while files:
                if len(current_batch) >= self.batch_size:
                    batches.append(current_batch)
                    current_batch = []

                # Add files from the same directory up to batch size
                space_left = self.batch_size - len(current_batch)
                to_add = files[:space_left]
                current_batch.extend(to_add)
                files = files[space_left:]

        # Add the last batch if not empty
        if current_batch:
            batches.append(current_batch)

        return batches

    def read_file_content(self, file_path: Path) -> str:
        """Read and return the content of a file."""
        try:
            full_path = self.base_path / file_path
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def analyze_batch(self, batch: List[Path]) -> Dict:
        """Send a batch of files to the LLM for analysis."""
        # Prepare the files data
        files_data = []
        for file_path in batch:
            content = self.read_file_content(file_path)
            files_data.append({
                "path": str(file_path),
                "content": content
            })

        # Group files by folder for module-specific descriptions
        folders = set(str(file_path.parent) for file_path in batch)

        # Prepare the prompt
        system_prompt = """
        You are a code analysis assistant. Your task is to analyze a batch of files 
        from a software project. Do not mention a batch in your analysis, that is
        just the way the files are processed.

        For each batch, you will:
        1. Provide a brief description of each file and its purpose
        2. Identify the modules or components these files belong to (by folder)
        3. Identify features implemented in these files
        4. Identify data models and their relationships
        5. Update a dependency graph showing relationships between files

        Your output should be in JSON format with the following structure:
        {
            "file_descriptions": {
                "file_path": "description of the file and its purpose"
            },
            "module_descriptions": {
                "folder_path": "description of the module or component in this folder"
            },
            "features": [
                "feature1", "feature2", "feature3"
            ],
            "data_models": {
                "model_name": {
                    "description": "description of what this data model represents",
                    "attributes": {
                        "attribute_name": "attribute type and description"
                    },
                    "relationships": [
                        "relationship with other models (e.g., 'has many users')"
                    ]
                }
            },
            "dependency_graph": "updated MermaidJS graph TD syntax showing dependencies.
                Focus on dependencies between source code and modules, ignore project
                setup & config files. Name the graph entities so that they're
                understandable. Prefer class/entities and if not found, use the
                module/file names. The graph entities must contain only text and no
                parentheses of any kind."
        }

        For the dependency graph:
        - Use MermaidJS graph TD syntax
        - Each node should be a file or module
        - Arrows should represent dependencies (A --> B means A depends on B)
        - Add new nodes and connections based on your analysis

        For data models:
        - Identify classes, structs, or other data structures that represent domain entities
        - Infer the purpose and relationships between these models
        - Include attributes/properties and their types when possible
        - Focus on business/domain models, not utility classes or implementation details

        For features:
        - Identify specific functionalities or capabilities implemented in the code
        - Be specific and concise (e.g., "User authentication", "Data visualization", "PDF export")
        - Focus on end-user or developer-facing features, not implementation details

        Keep your tone objective. Instead of giving a review or compliments on the
        codebase, focus on the structure and dependencies of the project.
        """

        # Create the user message with all the necessary information
        user_message = f"""
        # Project Analysis Request

        ## Files in Current Batch:
        {json.dumps(files_data, indent=2)}

        ## Files Processed So Far:
        {list(self.processed_files)}

        ## Current Dependency Graph:
        ```mermaid
        {self.dependency_graph}
        ```

        ## Folders in this batch:
        {list(folders)}

        Please analyze these files and provide:
        1. A description of each file
        2. A description of each module (folder) they belong to
        3. Identification of data models and their relationships
        4. An updated dependency graph in MermaidJS format

        Return your analysis in the JSON format specified in the system prompt.
        """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        print(f"Analyzing batch of {len(batch)} files...")

        # Call the LLM
        response = llm.chat.completions.create(
            model="o3-mini",  # Using o3-mini as specified
            messages=messages,
            max_completion_tokens=8192
        )

        # Extract and parse the response
        try:
            result = json.loads(response.choices[0].message.content)
            return result
        except json.JSONDecodeError:
            print("Error: Could not parse LLM response as JSON")
            print(response.choices[0].message.content)
            return {
                "file_descriptions": {},
                "module_descriptions": {},
                "data_models": {},
                "dependency_graph": self.dependency_graph
            }

    def generate_project_description(self):
        """Generate an overall project description based on all analyzed files."""
        print("\nGenerating overall project description...")

        system_prompt = """
        You are a code analysis assistant. Your task is to provide a comprehensive
        description of a software project based on the analysis of its files and modules.

        Provide a clear, concise overview that explains:
        1. The purpose and main functionality of the project
        2. The architecture and key components
        3. Data model and entity relationships
        4. Features
        5. How the components interact with each other
        6. Technologies and frameworks used
        7. Any notable patterns or design decisions

        Your description should be well-structured with appropriate headings and 
        should be suitable for inclusion in project documentation.

        Keep your tone objective. Instead of giving a review or compliments on the
        codebase, focus on the structure and dependencies of the project.
        """

        # Create a summary of module descriptions
        module_summary = "\n\n".join([
            f"**{folder}**: {description}" 
            for folder, description in sorted(self.module_descriptions.items())
        ])

        # Create a summary of features
        features_list = sorted(list(self.features))
        features_summary = "\n".join([f"- {feature}" for feature in features_list])

        # Create a summary of data models
        data_models_summary = ""
        for model_name, model_info in self.data_models.items():
            data_models_summary += f"\n\n### {model_name}\n"
            data_models_summary += f"{model_info.get('description', 'No description')}\n\n"

            # Add attributes
            if model_info.get('attributes'):
                data_models_summary += "**Attributes:**\n"
                for attr_name, attr_desc in model_info.get('attributes', {}).items():
                    data_models_summary += f"- {attr_name}: {attr_desc}\n"

            # Add relationships
            if model_info.get('relationships'):
                data_models_summary += "\n**Relationships:**\n"
                for rel in model_info.get('relationships', []):
                    data_models_summary += f"- {rel}\n"

        user_prompt = f"""
        I have analyzed a software project with the following structure:

        Module Descriptions:
        {module_summary}

        Data Models:
        {data_models_summary}

        Features:
        {features_summary}

        Dependency Graph:
        ```mermaid
        {self.dependency_graph}
        ```

        Please provide a well-structured project description that explains the
        purpose, architecture, data model, components, and technologies used in this project.
        Format your description as markdown.
        """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # Call the LLM
        response = llm.chat.completions.create(
            model="o3-mini",
            messages=messages,
            max_completion_tokens=8192
        )

        self.project_description = response.choices[0].message.content
        print("Project description generated successfully.")
        return self.project_description

    def process_project(self):
        """Process the entire project in batches."""
        # Collect all files
        self.collect_files()

        # Process files in batches
        while len(self.processed_files) < len(self.all_files):
            # Create batches from remaining files
            batches = self.create_batches()
            if not batches:
                break

            # Process each batch
            for batch_index, batch in enumerate(batches):
                # Print batch information
                print("\n" + "-"*80)
                print(f"Processing Batch #{batch_index+1} ({len(batch)} files):")
                for file_path in batch:
                    print(f"  - {file_path}")
                print("-"*80)

                # Analyze the batch
                result = self.analyze_batch(batch)

                # Update project information
                self.file_descriptions.update(result.get("file_descriptions", {}))

                # Update module descriptions (folder-specific)
                module_descriptions = result.get("module_descriptions", {})
                for folder, description in module_descriptions.items():
                    if description and description != "No description provided":
                        self.module_descriptions[folder] = description

                # Update features
                features = result.get("features", [])
                for feature in features:
                    if feature and feature.strip():
                        self.features.add(feature.strip())

                # Update data models
                data_models = result.get("data_models", {})
                for model_name, model_info in data_models.items():
                    if model_name not in self.data_models:
                        self.data_models[model_name] = model_info
                    else:
                        # Merge relationships
                        existing_relationships = set(self.data_models[model_name].get("relationships", []))
                        new_relationships = set(model_info.get("relationships", []))
                        merged_relationships = list(existing_relationships.union(new_relationships))
                        
                        # Merge attributes
                        existing_attributes = self.data_models[model_name].get("attributes", {})
                        new_attributes = model_info.get("attributes", {})
                        existing_attributes.update(new_attributes)
                        
                        # Update the model
                        self.data_models[model_name]["relationships"] = merged_relationships
                        self.data_models[model_name]["attributes"] = existing_attributes
                        
                        # Use the most detailed description
                        if len(model_info.get("description", "")) > len(self.data_models[model_name].get("description", "")):
                            self.data_models[model_name]["description"] = model_info["description"]

                # Update dependency graph
                self.dependency_graph = result.get(
                    "dependency_graph", self.dependency_graph
                )

                # Print the module descriptions for this batch
                print("\n" + "="*80)
                print("Module Descriptions:")
                for folder, description in module_descriptions.items():
                    if description:
                        print(f"\nModule: {folder}")
                        print(f"{description}")
                print("="*80)

                # Display the updated dependency graph
                print("\nCurrent Dependency Graph:")
                print("```mermaid")
                print(self.dependency_graph)
                print("```\n")

                # Mark files as processed
                for file in batch:
                    self.processed_files.add(file)

                # Print progress
                processed = len(self.processed_files)
                total = len(self.all_files)
                print(f"Processed {processed}/{total} files")

        # Generate overall project description
        self.generate_project_description()

        # Print final results
        print("\n" + "="*80)
        print("Project Analysis Complete")
        print("="*80)
        print("\nDependency Graph:")
        print("```mermaid")
        print(self.dependency_graph)
        print("```")

        # Save results to file
        self.save_results()

    def save_results(self):
        """Save analysis results to a single markdown file."""
        # Create the markdown content

        # Add project description
        markdown_content = self.project_description
        markdown_content += "\n\n"

        # Add module descriptions
        markdown_content += "## Module Descriptions\n\n"
        for folder, description in sorted(self.module_descriptions.items()):
            markdown_content += f"### {folder}\n\n"
            markdown_content += f"{description}\n\n"

        # Add dependency graph
        markdown_content += "## Project Dependency Graph\n\n"
        if not self.dependency_graph.startswith("```mermaid"):
            markdown_content += "```mermaid\n"
            markdown_content += self.dependency_graph
            markdown_content += "\n```\n\n"
        else:
            markdown_content += self.dependency_graph + "\n\n"

        # Add file descriptions
        markdown_content += "## File Descriptions\n\n"
        for file_path, description in sorted(self.file_descriptions.items()):
            markdown_content += f"### {file_path}\n\n"
            markdown_content += f"{description}\n\n"

        # Save to file
        # Create output directory if it doesn't exist
        os.makedirs("out", exist_ok=True)
        output_file = "out/project_analysis.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        print(f"\nResults saved to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a project's structure using LLM"
    )
    parser.add_argument("path", help="Path to the project directory")
    parser.add_argument(
        "--ext",
        required=True,
        help="File extensions to include (e.g., ts,js)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=15,
        help="Number of files to process in each batch"
    )
    args = parser.parse_args()

    # Convert extensions to glob patterns
    extensions = args.ext.split(',')
    include_patterns = ','.join(f'*.{ext.strip()}' for ext in extensions)
    analyzer = ProjectAnalyzer(args.path, include_patterns, args.batch_size)
    analyzer.process_project()


if __name__ == "__main__":
    main()
