import base64
import io

import matplotlib.pyplot as plt
import requests
from PIL import Image


def mermaid_to_image(graph_definition, filename="diagram.png", dpi=1200):
    graph_bytes = graph_definition.encode("utf8")
    base64_bytes = base64.urlsafe_b64encode(graph_bytes)
    base64_string = base64_bytes.decode("ascii")
    image_data = requests.get("https://mermaid.ink/img/" + base64_string).content

    img = Image.open(io.BytesIO(image_data))
    plt.imshow(img)
    plt.axis("off")
    plt.savefig(filename, dpi=dpi)


mermaid_code = """
graph LR
    A --> B
    B --> C
"""
mermaid_to_image(mermaid_code, "my_diagram.png")
