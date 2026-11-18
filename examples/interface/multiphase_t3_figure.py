from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle

OUT = Path(__file__).resolve().parent
BLUE = '#2F6F9F'
ORANGE = '#E08A3D'
RED = '#C43D4B'
INK = '#19334A'
MUTED = '#60778A'
LIGHT = '#F4F7FA'

nodes = {
    1: (0, 0), 2: (1, 0), 3: (1, 1), 4: (0, 1),
    5: (2, 0), 6: (2, 1), 7: (0, 2), 8: (1, 2), 9: (2, 2),
}
solid = {101: (1, 2, 3), 102: (4, 3, 8)}
liquid = {201: (2, 5, 3), 202: (3, 6, 8)}
interfaces = {203: (2, 3), 204: (3, 8)}

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11.5, 4.8), dpi=180)
fig.patch.set_facecolor('white')

for ax in (ax0, ax1):
    ax.set_aspect('equal')
    ax.set_xlim(-0.35, 2.35)
    ax.set_ylim(-0.35, 2.35)
    ax.axis('off')


def draw_mesh(ax, show_interfaces=False):
    for label, conn in solid.items():
        points = [nodes[node] for node in conn]
        ax.add_patch(Polygon(points, closed=True, facecolor=BLUE, alpha=0.24,
                             edgecolor=BLUE, linewidth=1.8))
        cx = sum(p[0] for p in points) / 3
        cy = sum(p[1] for p in points) / 3
        ax.text(cx, cy, f'S{label}', ha='center', va='center', fontsize=10,
                color=INK, fontweight='bold')
    for label, conn in liquid.items():
        points = [nodes[node] for node in conn]
        ax.add_patch(Polygon(points, closed=True, facecolor=ORANGE, alpha=0.27,
                             edgecolor=ORANGE, linewidth=1.8))
        cx = sum(p[0] for p in points) / 3
        cy = sum(p[1] for p in points) / 3
        ax.text(cx, cy, f'L{label}', ha='center', va='center', fontsize=10,
                color=INK, fontweight='bold')
    for label, (a, b) in nodes.items():
        ax.plot(*nodes[label], 'o', color=INK, markersize=3)
        ax.text(nodes[label][0] + 0.04, nodes[label][1] + 0.04, str(label), fontsize=7, color=MUTED)
    if show_interfaces:
        for label, (a, b) in interfaces.items():
            ax.plot([nodes[a][0], nodes[b][0]], [nodes[a][1], nodes[b][1]],
                    color=RED, linewidth=5, solid_capstyle='round')
            mx = (nodes[a][0] + nodes[b][0]) / 2
            my = (nodes[a][1] + nodes[b][1]) / 2
            ax.text(mx + 0.10, my, f'U2-{label}', color=RED, fontsize=9,
                    fontweight='bold', va='center')


draw_mesh(ax0, show_interfaces=False)
ax0.axvline(1, color=MUTED, linestyle=(0, (3, 3)), linewidth=1.2, alpha=0.8)
ax0.text(0.45, 2.22, 'Solid', ha='center', color=BLUE, fontsize=11, fontweight='bold')
ax0.text(1.55, 2.22, 'Liquid', ha='center', color=ORANGE, fontsize=11, fontweight='bold')
ax0.text(1.0, -0.25, 'Select solid elements first, then liquid elements', ha='center', color=MUTED, fontsize=9)
ax0.set_title('(a) Two-phase selection in Abaqus/CAE', loc='left', color=INK, fontweight='bold', pad=18)

# Connector arrow
arrow = FancyArrowPatch((0.46, 0.50), (0.54, 0.50), transform=fig.transFigure,
                        arrowstyle='-|>', mutation_scale=18, linewidth=2.2, color=RED)
fig.add_artist(arrow)
fig.text(0.50, 0.54, 'topology generator', ha='center', va='bottom', color=RED, fontsize=9, fontweight='bold')

draw_mesh(ax1, show_interfaces=True)
ax1.text(0.45, 2.22, 'Solid', ha='center', color=BLUE, fontsize=11, fontweight='bold')
ax1.text(1.55, 2.22, 'Liquid', ha='center', color=ORANGE, fontsize=11, fontweight='bold')
ax1.text(1.0, -0.25, 'Two cross-phase U2 interface elements are written to a new INP', ha='center', color=MUTED, fontsize=9)
ax1.set_title('(b) Generated PRIME interface topology', loc='left', color=INK, fontweight='bold', pad=18)

fig.text(0.5, 0.02, 'Solid elements 101–102 + liquid elements 201–202 → U2 elements 203–204',
         ha='center', color=INK, fontsize=11, fontweight='bold')
fig.subplots_adjust(left=0.03, right=0.97, bottom=0.10, top=0.88, wspace=0.20)
fig.savefig(OUT / 'multiphase_t3_interface.svg', format='svg', bbox_inches='tight')
fig.savefig(OUT / 'multiphase_t3_interface.png', format='png', dpi=220, bbox_inches='tight')
