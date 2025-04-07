import mermaid as md
from mermaid.graph import Graph

# Define the Mermaid diagram syntax
mermaid_syntax = """
erDiagram
    EMPLOYEES ||--o{ DEPARTMENTS : belongs_to
    EMPLOYEES {
        int emp_id PK
        string emp_name
        int dept_id FK NOT NULL
    }
    DEPARTMENTS {
        int dept_id PK
        string dept_name UNIQUE
        string location CHECK
    }
"""

# Create a Graph object with the syntax
graph = Graph("erdiagram", mermaid_syntax)

# Generate the SVG content directly from the Graph object
md.Mermaid(graph).to_svg("./support_apps/image/mermaid_diagram.svg")
md.Mermaid(graph).to_png("./support_apps/image/mermaid_diagram.png")


print("Mermaid diagram saved as mermaid_diagram")
