"""ORM models the RAG module reads/writes.

The RAG module owns NO tables of its own. To avoid duplicate ``Base.metadata``
registration, the table classes it consumes are re-exported from their owning
modules (single source of truth):

  - ``email_metadata`` / ``file_metadata`` (+ ``file_source`` enum)  -> app.models.ingestion
  - ``projects`` / ``project_files`` / ``project_sectors``           -> app.models.projects
  - ``workspace_instructions``                                       -> app.models.workspaces

rag_service imports ``WorkspaceInstructions`` (plural), so the owner's
``WorkspaceInstruction`` is aliased here.
"""

from app.models.ingestion import EmailMetadata, FileMetadata, FileSource, file_source_enum
from app.models.projects import Project, ProjectFile, ProjectSector
from app.models.workspaces import WorkspaceInstruction as WorkspaceInstructions

__all__ = [
    "EmailMetadata",
    "FileMetadata",
    "FileSource",
    "Project",
    "ProjectFile",
    "ProjectSector",
    "WorkspaceInstructions",
    "file_source_enum",
]
