from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ModelProfile:
    name: str
    base_url: str
    api_key: str
    intelligence_score: int  # 1-20
    cost_per_1k_input: float
    cost_per_1k_output: float
    context_window: int
    supports_tools: bool
    supports_streaming: bool


class ModelRouter:
    def __init__(self):
        self.models: List[ModelProfile] = []

    def add_model(self, profile: ModelProfile) -> None:
        """Register a model"""
        self.models.append(profile)

    def route(self, task_complexity: str = "medium") -> ModelProfile:
        """
        Pick best model for task complexity (simple/medium/hard)
        - simple tasks: pick cheapest model with intelligence >= 5
        - medium tasks: pick cheapest model with intelligence >= 10
        - hard tasks: pick cheapest model with intelligence >= 15
        - If no model meets threshold, pick highest available
        """
        if not self.models:
            raise ValueError("No models registered")

        # Define intelligence thresholds for each complexity level
        thresholds = {
            "simple": 5,
            "medium": 10,
            "hard": 15
        }

        min_intelligence = thresholds.get(task_complexity.lower(), 10)

        # Filter models that meet the intelligence requirement
        capable_models = [m for m in self.models if m.intelligence_score >= min_intelligence]

        # If no model meets the threshold, use all models
        if not capable_models:
            capable_models = self.models

        # Sort by total cost (input + output) per 1k tokens
        def calculate_total_cost(model):
            return model.cost_per_1k_input + model.cost_per_1k_output

        capable_models.sort(key=calculate_total_cost)

        return capable_models[0]

    def route_cheapest(self) -> ModelProfile:
        """Pick cheapest model"""
        if not self.models:
            raise ValueError("No models registered")

        def calculate_total_cost(model):
            return model.cost_per_1k_input + model.cost_per_1k_output

        sorted_models = sorted(self.models, key=calculate_total_cost)
        return sorted_models[0]

    def route_best(self) -> ModelProfile:
        """Pick highest intelligence model"""
        if not self.models:
            raise ValueError("No models registered")

        return max(self.models, key=lambda m: m.intelligence_score)

    def list_models(self) -> List[ModelProfile]:
        """Return list of all registered models"""
        return self.models.copy()

    def estimate_cost(self, input_tokens: int, output_tokens: int, model_name: str) -> float:
        """
        Estimate cost for a given number of input and output tokens for a specific model
        """
        model = next((m for m in self.models if m.name == model_name), None)
        if not model:
            raise ValueError(f"Model '{model_name}' not found")

        input_cost = (input_tokens / 1000) * model.cost_per_1k_input
        output_cost = (output_tokens / 1000) * model.cost_per_1k_output

        return input_cost + output_cost
