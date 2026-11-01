"""The exporter's one error type: every problem found is collected and raised together."""


class ExportError(Exception):
    """An Assembly that can't be exported. `problems` lists each one as a sentence for the user."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))
