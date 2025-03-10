# project-analyzer

An example that shows how LLMs can be used to analyze the structure of existing software. This example uses Azure OpenAI Service, so no code is sent to OpenAI but only handled within an enterprise Azure deployment.

## Examples

The `examples` folder has got a couple example analyses of code repositories:

- [java_example.md](examples/java_example.md) - Analysis of a Java backend service
- [jira_clone_analysis.md](examples/jira_clone_analysis.md) - Analysis of a Jira clone project

## Prerequisites
- Azure OpenAI Service with o3-mini deployed
- `az cli` logged into the correct Azure Subscription
- Permissions to use the service (Azure Role-based access is used)

## Setup

```
# Create a .env file, remember to set your Azure OpenAI base URL
cp .env.example .env

# Create virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the analysis on a repository

```
python project_analyzer --ext <file_extensions_to_include> <folder_to_analyze>

e.g.

python project_analyzer --ext ts,js ~/my_react_app
python project_analyzer --ext java ~/my_java_backend
```

If running the analysis, make sure to check the `IGNORE_DIRS` variable in [project_analyzer.py](project_analyzer.py) to check any folders (like `node_modules`) you may NOT want to include to the analysis are in the list.

The script does say at the beginning `Found X files to analyze` and asks for confirmation to proceed. If that number is high, like in the thousands, you may want to adjust your filters a bit. An average project usually has
dozens, max a few hundred code files. The script will ask for confirmation before starting
the analysis.