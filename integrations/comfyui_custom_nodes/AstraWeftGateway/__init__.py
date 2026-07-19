"""ComfyUI Custom Nodes that invoke AstraWeft through its loopback gateway."""

from .nodes import AstraWeftProviderImage, AstraWeftProviderJSON, AstraWeftProviderVideo

NODE_CLASS_MAPPINGS = {
    "AstraWeftProviderImage": AstraWeftProviderImage,
    "AstraWeftProviderJSON": AstraWeftProviderJSON,
    "AstraWeftProviderVideo": AstraWeftProviderVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AstraWeftProviderImage": "AstraWeft Provider Image",
    "AstraWeftProviderJSON": "AstraWeft Provider JSON",
    "AstraWeftProviderVideo": "AstraWeft Provider Video",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
