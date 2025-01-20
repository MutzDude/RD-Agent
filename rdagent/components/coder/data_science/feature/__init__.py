import json

from rdagent.components.coder.CoSTEER import CoSTEER
from rdagent.components.coder.CoSTEER.config import CoSTEER_SETTINGS
from rdagent.components.coder.CoSTEER.evaluators import CoSTEERMultiEvaluator
from rdagent.components.coder.CoSTEER.evolving_strategy import (
    MultiProcessEvolvingStrategy,
)
from rdagent.components.coder.CoSTEER.knowledge_management import (
    CoSTEERQueriedKnowledge,
)
from rdagent.components.coder.data_science.feature.eval import FeatureCoSTEEREvaluator
from rdagent.components.coder.data_science.feature.exp import FeatureTask
from rdagent.core.exception import CoderError
from rdagent.core.experiment import FBWorkspace
from rdagent.core.scenario import Scenario
from rdagent.oai.llm_utils import APIBackend
from rdagent.utils.agent.tpl import T


class FeatureMultiProcessEvolvingStrategy(MultiProcessEvolvingStrategy):
    def implement_one_task(
        self,
        target_task: FeatureTask,
        queried_knowledge: CoSTEERQueriedKnowledge | None = None,
        workspace: FBWorkspace | None = None,
    ) -> dict[str, str]:
        # return a workspace with "load_data.py", "spec/load_data.md" inside
        # assign the implemented code to the new workspace.
        feature_information_str = target_task.get_task_information()

        # 1. query
        queried_similar_successful_knowledge = (
            queried_knowledge.task_to_similar_task_successful_knowledge[feature_information_str]
            if queried_knowledge is not None
            else []
        )
        queried_former_failed_knowledge = (
            queried_knowledge.task_to_former_failed_traces[feature_information_str]
            if queried_knowledge is not None
            else []
        )
        latest_code_feedback = [
            knowledge.feedback
            for knowledge in queried_former_failed_knowledge[0]
            if knowledge.implementation.file_dict.get("feature.py") is not None
            and knowledge.implementation.file_dict.get("feature.py") == workspace.file_dict.get("feature.py")
        ]
        if len(latest_code_feedback) > 0:
            queried_former_failed_knowledge = (
                [
                    knowledge
                    for knowledge in queried_former_failed_knowledge[0]
                    if knowledge.implementation.file_dict.get("feature.py") != workspace.file_dict.get("feature.py")
                ],
                queried_former_failed_knowledge[1],
            )

        # 2. code
        system_prompt = T(".prompts:feature.system").r(
            task_desc=feature_information_str,
            data_loader_code=workspace.file_dict.get("load_data.py"),
            queried_similar_successful_knowledge=queried_similar_successful_knowledge,
            queried_former_failed_knowledge=queried_former_failed_knowledge[0],
        )
        user_prompt = T(".prompts:feature.user").r(
            feature_spec=workspace.file_dict["spec/feature.md"],
            latest_code=workspace.file_dict.get("feature.py"),
            latest_code_feedback=latest_code_feedback[0] if len(latest_code_feedback) > 0 else None,
        )

        for _ in range(5):
            feature_code = json.loads(
                APIBackend().build_messages_and_create_chat_completion(
                    user_prompt=user_prompt, system_prompt=system_prompt, json_mode=True
                )
            )["code"]
            if feature_code != workspace.file_dict.get("feature.py"):
                break
            else:
                user_prompt = user_prompt + "\nPlease avoid generating same code to former code!"
        else:
            raise CoderError("Failed to generate a new feature code.")

        return {
            "feature.py": feature_code,
        }

    def assign_code_list_to_evo(self, code_list: list[dict[str, str]], evo):
        """
        Assign the code list to the evolving item.

        The code list is aligned with the evolving item's sub-tasks.
        If a task is not implemented, put a None in the list.
        """
        for index in range(len(evo.sub_tasks)):
            if code_list[index] is None:
                continue
            if evo.sub_workspace_list[index] is None:
                # evo.sub_workspace_list[index] = FBWorkspace(target_task=evo.sub_tasks[index])
                evo.sub_workspace_list[index] = evo.experiment_workspace
            evo.sub_workspace_list[index].inject_files(**code_list[index])
        return evo


class FeatureCoSTEER(CoSTEER):
    def __init__(
        self,
        scen: Scenario,
        *args,
        **kwargs,
    ) -> None:
        eva = CoSTEERMultiEvaluator(
            FeatureCoSTEEREvaluator(scen=scen), scen=scen
        )  # Please specify whether you agree running your eva in parallel or not
        es = FeatureMultiProcessEvolvingStrategy(scen=scen, settings=CoSTEER_SETTINGS)

        super().__init__(*args, settings=CoSTEER_SETTINGS, eva=eva, es=es, evolving_version=2, scen=scen, **kwargs)