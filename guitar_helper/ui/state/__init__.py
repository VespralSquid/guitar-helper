"""Qt-free UI state. Explicitly a package, not a namespace package: freezers
resolve implicit namespace packages inconsistently, and this one is imported
only indirectly (via main_window), which is exactly the case that gets missed.
"""
