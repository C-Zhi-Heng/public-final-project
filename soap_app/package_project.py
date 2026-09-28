import os
import json
from datetime import datetime

# Define the folders to scan relative to where this script runs
TARGET_FOLDERS = {
    "project_plans": "./plan.md",
    "test_logs": "./test-logs.md",
    "bug_reports": "./issues-log.md",
    "python_code": "./",
}
OUTPUT_FILE = "claude_project_context.json"

def read_file_content(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        return f"[Error reading file: {str(e)}]"

def package_project():
    project_payload = {
        "generated_at": datetime.now().isoformat(),
        "files": []
    }
    
    print("📦 Starting project packaging for Claude...")
    
    for category, folder_path in TARGET_FOLDERS.items():
        if not os.path.exists(folder_path):
            print(f"⚠️ Warning: Folder '{folder_path}' not found. Skipping.")
            continue
            
        print(f"🔍 Scanning {category} in '{folder_path}'...")
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.endswith(('.md', '.py', '.txt', '.log')):
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path)
                    
                    # Overwrite category based on file extension
                    active_category = category
                    if file.endswith('.py'):
                        active_category = "python_code"
                    elif file.endswith('.md'):
                        active_category = "markdown_documentation"
                    elif file.endswith(('.log', '.txt')):
                        active_category = "system_test_logs"
                    
                    content = read_file_content(full_path)
                    
                    project_payload["files"].append({
                        "category": active_category, # Uses the extension-based category
                        "filename": file,
                        "path": relative_path,
                        "content": content
                    })

    # Save the consolidated payload
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(project_payload, f, indent=2, ensure_ascii=False)
        
    print(f"✅ Success! Packaged file created: {OUTPUT_FILE}")
    print(f"Size: {os.path.getsize(OUTPUT_FILE) / 1024:.2f} KB")

if __name__ == "__main__":
    package_project()
