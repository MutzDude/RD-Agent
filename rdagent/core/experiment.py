from __future__ import annotations

import os
import platform
import re
import shutil
import typing
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Generic, TypeVar

from rdagent.core.conf import RD_AGENT_SETTINGS
from rdagent.utils import filter_progress_bar

if typing.TYPE_CHECKING:
    from rdagent.core.proposal import Hypothesis
    from rdagent.utils.env import Env

"""
This file contains the all the class about organizing the task in RD-Agent.
"""


class AbsTask(ABC):
    def __init__(self, name: str, version: int = 1) -> None:
        """
        The version of the task, default is 1
        Because qlib tasks execution and kaggle tasks execution are different, we need to distinguish them.
        TODO: We may align them in the future.
        """
        self.version = version
        self.name = name

    @abstractmethod
    def get_task_information(self) -> str:
        """
        Get the task information string to build the unique key
        """


class Task(AbsTask):
    def __init__(self, name: str, version: int = 1, description: str = "") -> None:
        super().__init__(name, version)
        self.description = description

    def get_task_information(self) -> str:
        return f"Task Name: {self.name}\nDescription: {self.description}"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.name}>"


ASpecificTask = TypeVar("ASpecificTask", bound=Task)


class Workspace(ABC, Generic[ASpecificTask]):
    """
    A workspace is a place to store the task implementation. It evolves as the developer implements the task.
    To get a snapshot of the workspace, make sure call `copy` to get a copy of the workspace.
    """

    def __init__(self, target_task: ASpecificTask | None = None) -> None:
        self.target_task: ASpecificTask | None = target_task

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> object | None:
        error_message = "execute method is not implemented."
        raise NotImplementedError(error_message)

    @abstractmethod
    def copy(self) -> Workspace:
        error_message = "copy method is not implemented."
        raise NotImplementedError(error_message)

    @property
    @abstractmethod
    def all_codes(self) -> str:
        """
        Get all the code files in the workspace as a single string.
        """


ASpecificWS = TypeVar("ASpecificWS", bound=Workspace)


class WsLoader(ABC, Generic[ASpecificTask, ASpecificWS]):
    @abstractmethod
    def load(self, task: ASpecificTask) -> ASpecificWS:
        error_message = "load method is not implemented."
        raise NotImplementedError(error_message)


class FBWorkspace(Workspace):
    """
    File-based task workspace

    The implemented task will be a folder which contains related elements.
    - Data
    - Code Workspace
    - Output
        - After execution, it will generate the final output as file.

    A typical way to run the pipeline of FBWorkspace will be:
    (We didn't add it as a method due to that we may pass arguments into
    `prepare` or `execute` based on our requirements.)

    .. code-block:: python

        def run_pipeline(self, **files: str):
            self.prepare()
            self.inject_files(**files)
            self.execute()

    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.file_dict: dict[str, Any] = (
            {}
        )  # The code injected into the folder, store them in the variable to reproduce the former result
        self.workspace_path: Path = RD_AGENT_SETTINGS.workspace_path / uuid.uuid4().hex

    @staticmethod
    def _format_code_dict(code_dict: dict[str, str]) -> str:
        """
        Helper function to format the code dictionary into a string.
        """
        code_string = ""
        for file_name, code in code_dict.items():
            code_string += f"\nFile Path: {file_name}\n```\n{code}\n```"
        return code_string

    @property
    def all_codes(self) -> str:
        """
        Get all the code files in the workspace as a single string, excluding test files.
        """
        filtered_dict = {k: v for k, v in self.file_dict.items() if k.endswith(".py") and "test" not in k}
        return self._format_code_dict(filtered_dict)

    def get_codes(self, pattern: str) -> str:
        """
        Get code files matching a specific pattern as a single string, excluding test files.
        """
        filtered_dict = {
            k: v for k, v in self.file_dict.items() if re.search(pattern, k) and k.endswith(".py") and "test" not in k
        }
        return self._format_code_dict(filtered_dict)

    def prepare(self) -> None:
        """
        Prepare the workspace except the injected code
        - Data
        - Documentation
            typical usage of `*args, **kwargs`:
                Different methods shares the same data. The data are passed by the arguments.
        """
        self.workspace_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def link_all_files_in_folder_to_workspace(data_path: Path, workspace_path: Path) -> None:
        data_path = Path(data_path).absolute()  # in case of relative path that will be invalid when we change cwd.
        workspace_path = Path(workspace_path)
        for data_file_path in data_path.iterdir():
            workspace_data_file_path = workspace_path / data_file_path.name
            if workspace_data_file_path.exists():
                workspace_data_file_path.unlink()
            if platform.system() == "Linux":
                os.symlink(data_file_path, workspace_data_file_path)
            if platform.system() == "Windows":
                os.link(data_file_path, workspace_data_file_path)

    DEL_KEY = "__DEL__"

    def inject_files(self, **files: str) -> None:
        """
        Inject the code into the folder.
        {
            <file name1>: <code>,  // indicate writing <code> into <file name>
                          (create new file or replace existing file)
            <file name2>: "__DEL__"  // indicate removing file name2. When we want to replace a file to a new one,
                          we usually use this
        }
        """
        self.prepare()
        for k, v in files.items():
            target_file_path = self.workspace_path / k  # Define target_file_path before using it
            if v == self.DEL_KEY:  # Use self.DEL_KEY to access the class variable
                if target_file_path.exists():
                    target_file_path.unlink()  # Unlink the file if it exists
                self.file_dict.pop(k, None)  # Safely remove the key from file_dict
            else:
                self.file_dict[k] = v
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                target_file_path.write_text(v)

    def get_files(self) -> list[Path]:
        """
        Get the environment description.

        To be general, we only return a list of filenames.
        How to summarize the environment is the responsibility of the Developer.
        """
        return list(self.workspace_path.iterdir())

    def inject_code_from_folder(self, folder_path: Path) -> None:
        """
        Load the workspace from the folder
        """
        for file_path in folder_path.rglob("*"):
            if file_path.suffix in (".py", ".yaml", ".md"):
                relative_path = file_path.relative_to(folder_path)
                self.inject_files(**{str(relative_path): file_path.read_text()})

    def copy(self) -> FBWorkspace:
        """
        copy the workspace from the original one
        """
        return deepcopy(self)

    def clear(self) -> None:
        """
        Clear the workspace
        """
        shutil.rmtree(self.workspace_path, ignore_errors=True)
        self.file_dict = {}

    def execute(self, env: Env | None = None, entry: str | None = None) -> object | None:
        """
        Before each execution, make sure to prepare and inject code
        """
        self.prepare()
        self.inject_files(**self.file_dict)
        # TODO: env should be not None in new design (no code can run without environment)
        if env is not None and entry is not None:
            return filter_progress_bar(env.run(entry, str(self.workspace_path)))
        return None

    def __str__(self) -> str:
        return f"Workspace[{self.workspace_path=}" + (
            "]" if self.target_task is None else f",{self.target_task.name=}]"
        )


ASpecificWSForExperiment = TypeVar("ASpecificWSForExperiment", bound=Workspace)
ASpecificWSForSubTasks = TypeVar("ASpecificWSForSubTasks", bound=Workspace)


class Experiment(
    ABC,
    Generic[ASpecificTask, ASpecificWSForExperiment, ASpecificWSForSubTasks],
):
    """
    The experiment is a sequence of tasks and the implementations of the tasks after generated by the Developer.
    """

    def __init__(
        self,
        sub_tasks: Sequence[ASpecificTask],
        based_experiments: Sequence[ASpecificWSForExperiment] = [],
        hypothesis: Hypothesis | None = None,
    ) -> None:
        self.hypothesis: Hypothesis | None = hypothesis  # Experiment is optionally generated by hypothesis
        self.sub_tasks: Sequence[ASpecificTask] = sub_tasks
        self.sub_workspace_list: list[ASpecificWSForSubTasks | None] = [None] * len(self.sub_tasks)
        # TODO:
        # It will be used in runner in history
        # If we implement the whole workflow, we don't have to use it, then we remove it.
        self.based_experiments: Sequence[ASpecificWSForExperiment] = based_experiments

        self.result: object = None  # The result of the experiment, can be different types in different scenarios.
        self.sub_results: dict[str, float] = (
            {}
        )  # TODO: in Kaggle, now sub results are all saved in self.result, remove this in the future.
        self.experiment_workspace: ASpecificWSForExperiment | None = None


ASpecificExp = TypeVar("ASpecificExp", bound=Experiment)

TaskOrExperiment = TypeVar("TaskOrExperiment", Task, Experiment)


class Loader(ABC, Generic[TaskOrExperiment]):
    @abstractmethod
    def load(self, *args: Any, **kwargs: Any) -> TaskOrExperiment:
        err_msg = "load method is not implemented."
        raise NotImplementedError(err_msg)
