"""Top-level application pages."""

from astraweft.presentation.pages.artifacts import ArtifactsPage
from astraweft.presentation.pages.comfyui import ComfyUIPage
from astraweft.presentation.pages.costs import CostAnalysisPage
from astraweft.presentation.pages.dashboard import DashboardPage
from astraweft.presentation.pages.foundation import FoundationPage
from astraweft.presentation.pages.logs import RequestLogsPage
from astraweft.presentation.pages.models import ModelsPage
from astraweft.presentation.pages.playground import PlaygroundPage
from astraweft.presentation.pages.providers import ProviderPage
from astraweft.presentation.pages.settings import SettingsPage
from astraweft.presentation.pages.tasks import TaskCenterPage
from astraweft.presentation.pages.workflows import WorkflowPage

__all__ = [
    "ArtifactsPage",
    "ComfyUIPage",
    "CostAnalysisPage",
    "DashboardPage",
    "FoundationPage",
    "ModelsPage",
    "PlaygroundPage",
    "ProviderPage",
    "RequestLogsPage",
    "SettingsPage",
    "TaskCenterPage",
    "WorkflowPage",
]
