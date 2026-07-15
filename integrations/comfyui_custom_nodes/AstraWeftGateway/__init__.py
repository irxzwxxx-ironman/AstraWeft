"""ComfyUI Custom Nodes that invoke AstraWeft through its loopback gateway."""

from .nodes import AstraWeftProviderImage, AstraWeftProviderVideo

NODE_CLASS_MAPPINGS = {
    "AstraWeftProviderImage": AstraWeftProviderImage,
    "AstraWeftProviderVideo": AstraWeftProviderVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AstraWeftProviderImage": "AstraWeft Provider Image",
    "AstraWeftProviderVideo": "AstraWeft Provider Video",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
