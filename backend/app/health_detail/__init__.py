"""QRadar health in detail (D-42): the stored disk, system, pipeline and notification windows.

`spec` names what is asked of QRadar, `queries` builds the two aggregating searches, `rows`
parses their answers, and `fold` reduces the rows to the bounded aggregates that are stored.
Nothing here touches the database or a clock.
"""
