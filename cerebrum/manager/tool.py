import importlib
import os
import json
import base64
import subprocess
import sys
from typing import List, Dict, Optional, Tuple
import requests
from pathlib import Path
import platformdirs
import importlib.util

from cerebrum.manager.package import ToolPackage
from cerebrum.tool.core.registry import PATHS
import uuid

class ToolManager:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.cache_dir = Path(platformdirs.user_cache_dir("cerebrum_tools"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up local tools directory - Required change
        current_file = Path(__file__).resolve()
        cerebrum_root = current_file.parent.parent
        self.local_tools_dir = cerebrum_root / "example" / "tools"
        print(f"Initialized ToolManager with local tools directory: {self.local_tools_dir}")

    def _version_to_path(self, version: str) -> str:
        if version is None:
            return "latest"
        return str(version).replace(".", "-")

    def _path_to_version(self, path_version: str) -> str:
        return path_version.replace("-", ".")

    def package_tool(self, folder_path: str) -> Dict:
        """Package a tool from a folder into a transportable format."""
        tool_files = self._get_tool_files(folder_path)
        metadata = self._get_tool_metadata(folder_path)

        # Validate required tool metadata
        # required_fields = ['name', 'version', 'author']
        # missing_fields = [field for field in required_fields
        #                  if not metadata.get("meta", {}).get(field)]
        # if missing_fields:
        #     raise ValueError(f"Missing required metadata fields: {missing_fields}")

        return {
            "author": metadata.get("meta", {}).get("author"),
            "name": metadata.get("name"),
            "version": metadata.get("meta", {}).get("version"),
            "license": metadata.get("license", "Unknown"),
            "files": tool_files,
            "entry": metadata.get("build", {}).get("entry", "tool.py"),
            "module": metadata.get("build", {}).get("module", "Tool"),
        }
        
    def _get_random_cache_path(self):
        """
        Creates a randomly named folder inside the cache/cerebrum directory and returns its path.
        Uses platformdirs for correct cross-platform cache directory handling.
        """
        # Get the user cache directory using platformdirs
        cache_dir = platformdirs.user_cache_dir(appname="cerebrum_tools")

        # Generate a random UUID for the folder name
        random_name = str(uuid.uuid4())

        # Create the full path
        random_folder_path = os.path.join(cache_dir, random_name)

        # Create the directory and any necessary parent directories
        os.makedirs(random_folder_path, exist_ok=True)

        return Path(random_folder_path) / f"local.tool"

    def upload_tool(self, payload: Dict):
        """Upload a tool to the remote server."""
        response = requests.post(f"{self.base_url}/cerebrum/tools/upload", json=payload)
        response.raise_for_status()
        print(
            f"Tool {payload.get('author')}/{payload.get('name')} (v{payload.get('version')}) uploaded successfully."
        )

    def download_tool(
        self, author: str, name: str, version: str | None = None
    ) -> tuple[str, str, str]:
        """Download a tool from the remote server."""
        if version is None:
            cached_versions = self._get_cached_versions(author, name)
            version = self._get_newest_version(cached_versions)

        try:
            cache_path = self._get_cache_path(author, name, version)
        except:
            cache_path = None

        if cache_path is not None and cache_path.exists():
            print(f"Using cached version of tool {author}/{name} (v{version})")
            return author, name, version

        params = (
            {"author": author, "name": name, "version": version}
            if version
            else {"author": author, "name": name}
        )

        response = requests.get(
            f"{self.base_url}/cerebrum/tools/download", params=params
        )
        response.raise_for_status()
        tool_data = response.json()

        actual_version = tool_data.get("version", version)
        cache_path = self._get_cache_path(author, name, actual_version)

        self._save_tool_to_cache(tool_data, cache_path)
        print(
            f"Tool {author}/{name} (v{actual_version}) downloaded and cached successfully."
        )

        if not self.check_reqs_installed(cache_path):
            self.install_tool_reqs(cache_path)

        return author, name, actual_version

    def load_tool(
        self,
        author: str = "",
        name: str = "",
        version: str | None = None,
        local: bool = False,
    ):
        """Load a tool dynamically and return its class and configuration."""
        try:
            if local:
                # 直接从本地工具目录加载
                tool_path = self.local_tools_dir / name
                if not tool_path.exists():
                    raise FileNotFoundError(f"Local tool not found: {name}")
                
                # 读取配置文件
                config_path = tool_path / "config.json"
                with open(config_path) as f:
                    tool_config = json.load(f)
                
                # 获取入口文件和模块名
                entry_point = tool_config["build"]["entry"]
                module_name = tool_config["build"]["module"]
                
                # 将工具目录添加到sys.path
                current_path = str(Path.cwd())
                if current_path not in sys.path:
                    sys.path.insert(0, current_path)
                
                sys.path.insert(0, str(tool_path))
                
                try:
                    # 加载模块
                    spec = importlib.util.spec_from_file_location(
                        module_name,
                        str(tool_path / entry_point),
                        submodule_search_locations=[str(tool_path)] + sys.path
                    )
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    
                    # 获取工具类
                    tool_class = getattr(module, module_name)
                    return tool_class, tool_config
                    
                finally:
                    # 清理sys.path
                    sys.path.pop(0)
                    if current_path == sys.path[0]:
                        sys.path.pop(0)
                    
                    # 从sys.modules中移除
                    if module_name in sys.modules:
                        del sys.modules[module_name]
                    
            else:
                # 原有的远程工具加载逻辑
                if version is None:
                    cached_versions = self._get_cached_versions(author, name)
                    version = self._get_newest_version(cached_versions)

                tool_path = self._get_cache_path(author, name, version)

                if not tool_path.exists():
                    print(f"Tool {author}/{name} (v{version}) not found in cache. Downloading...")
                    self.download_tool(author, name, version)

                tool_package = ToolPackage(tool_path)
                tool_package.load()
                
                # 剩余的远程工具加载代码...
                
        except Exception as e:
            print(f"Error loading tool {name}: {str(e)}")
            raise

    def _get_cached_versions(self, author: str, name: str) -> List[str]:
        """Get list of cached versions for a tool."""
        tool_dir = self.cache_dir / author / name
        if tool_dir.exists():
            return [
                self._path_to_version(v.stem)
                for v in tool_dir.glob("*.tool")
                if v.is_file()
            ]
        return []

    def _get_newest_version(self, versions: List[str]) -> Optional[str]:
        """Get the newest version from a list of versions."""
        if not versions:
            return None
        # Simple version comparison (you might want to use packaging.version for more robust comparison)
        return sorted(versions, key=lambda v: [int(x) for x in v.split(".")])[-1]

    def _get_cache_path(self, author: str, name: str, version: str) -> Path:
        """Get the cache path for a tool."""
        return self.cache_dir / author / name / f"{self._version_to_path(version)}.tool"

    def _save_tool_to_cache(self, tool_data: Dict, cache_path: Path):
        """Save a tool to the local cache."""
        tool_package = ToolPackage(cache_path)
        tool_package.metadata = {
            "author": tool_data["author"],
            "name": tool_data["name"],
            "version": tool_data["version"],
            "license": tool_data["license"],
            "entry": tool_data["entry"],
            "module": tool_data["module"],
        }
        tool_package.files = {
            file["path"]: base64.b64decode(file["content"])
            for file in tool_data["files"]
        }
        tool_package.save()

    def _get_tool_files(self, folder_path: str) -> List[Dict[str, str]]:
        """Get all files from a tool folder."""
        files = []
        for root, _, filenames in os.walk(folder_path):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, folder_path)
                with open(file_path, "rb") as f:
                    content = base64.b64encode(f.read()).decode("utf-8")
                files.append({"path": relative_path, "content": content})
        return files

    def _get_tool_metadata(self, folder_path: str) -> Dict[str, str]:
        """Get tool metadata from config file."""
        config_path = os.path.join(folder_path, "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                return json.load(f)
        return {}

    def check_reqs_installed(self, tool_path: Path) -> bool:
        """Check if tool requirements are installed."""
        tool_package = ToolPackage(tool_path)
        tool_package.load()
        reqs_content = tool_package.files.get("requirements.txt")

        if not reqs_content:
            return True  # No requirements file

        try:
            result = subprocess.run(
                ["pip", "list", "--format=freeze"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            installed_packages = [
                line.split("==")[0].lower()
                for line in result.stdout.decode("utf-8").splitlines()
            ]
            required_packages = [
                line.strip().split("==")[0].lower()
                for line in reqs_content.decode("utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
            return all(req in installed_packages for req in required_packages)
        except Exception as e:
            print(f"Error checking requirements: {e}")
            return False

    def install_tool_reqs(self, tool_path: Path):
        """Install tool requirements."""
        tool_package = ToolPackage(tool_path)
        tool_package.load()
        reqs_content = tool_package.files.get("requirements.txt")

        if not reqs_content:
            print("No requirements.txt found. Skipping dependency installation.")
            return

        # Create temporary requirements file
        temp_reqs_path = self.cache_dir / "temp_requirements.txt"
        with open(temp_reqs_path, "wb") as f:
            f.write(reqs_content)

        # Install requirements
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-r", str(temp_reqs_path)]
            )
            print("Tool requirements installed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Error installing requirements: {e}")
        finally:
            temp_reqs_path.unlink(missing_ok=True)

    def list_available_tools(self) -> List[Dict[str, str]]:
        """List all available tools from the remote server."""
        response = requests.get(f"{self.base_url}/cerebrum/tools/list")
        response.raise_for_status()
        tools = response.json()
        return [
            {
                "tool": f"{tool['author']}/{tool['name']}/{tool['version']}",
                "type": tool.get("tool_type", "generic"),
                "description": tool.get("description", ""),
            }
            for tool in tools
        ]

    def check_tool_updates(self, author: str, name: str, current_version: str) -> bool:
        """Check if updates are available for a tool."""
        response = requests.get(
            f"{self.base_url}/cerebrum/tools/check_updates",
            params={"author": author, "name": name, "current_version": current_version},
        )
        response.raise_for_status()
        return response.json()["update_available"]

    def load_local_tool(self, name: str):
        """Load tool from local directory"""
        try:
            tool_path = self.local_tools_dir / name
            if not tool_path.exists():
                raise FileNotFoundError(f"Tool {name} not found in local directory")
            
            config_path = tool_path / "config.json"
            if not config_path.exists():
                raise FileNotFoundError(f"Config file not found for tool {name}")
                
            with open(config_path) as f:
                config = json.load(f)
                
            return config
            
        except Exception as e:
            print(f"Error loading local tool {name}: {str(e)}")
            raise
