"""
park_factors.py - MLB ballpark run environment factors.

Values are 3-year weighted run park factors sourced from FanGraphs
(fangraphs.com/guts.aspx, Park Factors table), normalized from the
100-scale to a 1.0 multiplier (1.0 = perfectly neutral).

Applied to ER and H rate in pitcher projections — a 1.16 factor means
roughly 16% more runs expected vs a neutral park. Parks don't change
quickly so these are stable enough for fantasy use season-to-season.
"""

# Team abbreviation → run park factor (MLB Stats API abbreviations)
PARK_FACTORS = {
    # Hitter-friendly
    'COL': 1.16,  # Coors Field — extreme outlier, always flag this
    'CIN': 1.08,  # Great American Ball Park
    'HOU': 1.05,  # Minute Maid Park
    'BOS': 1.05,  # Fenway Park
    'PHI': 1.04,  # Citizens Bank Park
    'MIL': 1.04,  # American Family Field
    'ARI': 1.04,  # Chase Field
    'TEX': 1.03,  # Globe Life Field
    'CHC': 1.02,  # Wrigley Field
    'ATL': 1.02,  # Truist Park
    'NYY': 1.02,  # Yankee Stadium
    # Near-neutral
    'LAA': 1.01,  # Angel Stadium
    'DET': 1.00,  # Comerica Park
    'CHW': 0.99,  # Guaranteed Rate Field
    'CLE': 0.99,  # Progressive Field
    'MIN': 0.99,  # Target Field
    'TOR': 0.99,  # Rogers Centre
    'STL': 0.98,  # Busch Stadium
    'NYM': 0.98,  # Citi Field
    'KC':  0.98,  # Kauffman Stadium
    # Pitcher-friendly
    'WSH': 0.97,  # Nationals Park
    'BAL': 0.97,  # Camden Yards
    'ATH': 0.97,  # Athletics
    'TB':  0.97,  # Tropicana Field
    'SF':  0.96,  # Oracle Park
    'MIA': 0.96,  # loanDepot Park
    'LAD': 0.96,  # Dodger Stadium
    'SEA': 0.95,  # T-Mobile Park
    'SD':  0.95,  # Petco Park
    'PIT': 0.95,  # PNC Park
}


def get_park_factor(row):
    """Return run park factor for the home stadium in this game row."""
    home_team = row.get('team') if row.get('home') else row.get('opponent', '')
    return PARK_FACTORS.get(home_team, 1.0)


def park_label(pf):
    """3-char display label for park environment (for monospace Discord output)."""
    if pf >= 1.10: return "COO"   # Coors — call it out explicitly
    if pf >= 1.04: return " ++"   # strong hitter park
    if pf >= 1.01: return "  +"   # mild hitter park
    if pf <= 0.95: return " --"   # strong pitcher park
    if pf <= 0.97: return "  -"   # mild pitcher park
    return "   "                   # neutral
