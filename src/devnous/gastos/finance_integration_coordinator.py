"""
Finance Integration Coordinator

Coordinates multi-agent team to integrate expense and CFDI generation system
from finance directory into samchat.

Uses MultiAgentCoordinator to manage specialized agents:
- Project Manager: Planning and coordination
- Backend Engineer: API and webhook implementation
- Frontend Engineer: Admin interface development
- Database Engineer: Schema alignment and migration
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import json

from ..response.multi_agent_coordinator import (
    MultiAgentCoordinator,
    AgentRole
)
from ..models import UserContext, EmotionalState

logger = logging.getLogger(__name__)


class FinanceIntegrationCoordinator:
    """
    Coordinates the finance integration project using multi-agent system.
    
    Manages:
    - Task assignment to specialized agents
    - Progress tracking
    - Agent communications logging
    - Workspace file updates
    """
    
    def __init__(self, workspace_file_path: str = None):
        """Initialize the finance integration coordinator."""
        self.coordinator = MultiAgentCoordinator()
        self.workspace_file = Path(workspace_file_path or "/root/samchat/FINANCE_INTEGRATION_WORKSPACE.md")
        
        # Project state
        self.project_status = {
            "phase": "setup",
            "tasks_completed": [],
            "tasks_in_progress": [],
            "tasks_pending": [],
            "issues": [],
            "blockers": []
        }
        
        # Agent assignments
        self.agent_assignments = {
            "project_manager": AgentRole.PROJECT_MANAGER,
            "backend_engineer": AgentRole.BACKEND_ENGINEER,
            "frontend_engineer": AgentRole.FRONTEND_ENGINEER,
            "database_engineer": AgentRole.DATABASE_ENGINEER,
            "qa": AgentRole.QUALITY_ASSURANCE
        }
        
        logger.info("Finance Integration Coordinator initialized")
    
    async def log_to_workspace(
        self,
        agent_role: str,
        action: str,
        details: str,
        next_steps: Optional[str] = None
    ):
        """Log agent communication to workspace file."""
        try:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            
            log_entry = f"""
### {timestamp} - {agent_role}

**Action:** {action}

**Details:**
{details}

"""
            if next_steps:
                log_entry += f"**Next Steps:**\n{next_steps}\n"
            
            log_entry += "---\n"
            
            # Read current workspace content
            if self.workspace_file.exists():
                content = self.workspace_file.read_text(encoding='utf-8')
            else:
                content = ""
            
            # Find Agent Communications section and append
            if "## Agent Communications" in content:
                # Insert after the section header
                parts = content.split("## Agent Communications", 1)
                if len(parts) == 2:
                    # Find where to insert (after "### Log Format" section if it exists)
                    section_content = parts[1]
                    if "### Log Format" in section_content:
                        # Insert after Log Format section
                        log_format_end = section_content.find("---", section_content.find("### Log Format"))
                        if log_format_end != -1:
                            insert_pos = log_format_end + 3
                            new_content = (
                                section_content[:insert_pos] + 
                                "\n" + log_entry + 
                                section_content[insert_pos:]
                            )
                        else:
                            new_content = log_entry + section_content
                    else:
                        new_content = log_entry + section_content
                    
                    content = parts[0] + "## Agent Communications" + new_content
                else:
                    content += "\n## Agent Communications\n" + log_entry
            else:
                content += "\n## Agent Communications\n" + log_entry
            
            # Write back
            self.workspace_file.write_text(content, encoding='utf-8')
            logger.info(f"Logged to workspace: {agent_role} - {action}")
            
        except Exception as e:
            logger.error(f"Error logging to workspace: {e}", exc_info=True)
    
    async def assign_task(
        self,
        task_name: str,
        agent_role: AgentRole,
        description: str,
        dependencies: List[str] = None
    ) -> Dict[str, Any]:
        """Assign a task to a specific agent."""
        task = {
            "name": task_name,
            "agent": agent_role.value,
            "description": description,
            "status": "assigned",
            "dependencies": dependencies or [],
            "assigned_at": datetime.utcnow().isoformat()
        }
        
        self.project_status["tasks_pending"].append(task)
        
        await self.log_to_workspace(
            agent_role=AgentRole.PROJECT_MANAGER.value,
            action=f"Assigned task: {task_name}",
            details=f"Task assigned to {agent_role.value}.\n\n**Description:** {description}",
            next_steps=f"{agent_role.value} should begin work on this task"
        )
        
        return task
    
    async def mark_task_in_progress(self, task_name: str, agent_role: str):
        """Mark a task as in progress."""
        # Move from pending to in_progress
        for task in self.project_status["tasks_pending"]:
            if task["name"] == task_name:
                task["status"] = "in_progress"
                task["started_at"] = datetime.utcnow().isoformat()
                self.project_status["tasks_in_progress"].append(task)
                self.project_status["tasks_pending"].remove(task)
                
                await self.log_to_workspace(
                    agent_role=agent_role,
                    action=f"Started task: {task_name}",
                    details=f"Beginning work on {task_name}",
                    next_steps="Continue implementation"
                )
                break
    
    async def mark_task_completed(
        self,
        task_name: str,
        agent_role: str,
        summary: str,
        files_created: List[str] = None,
        files_modified: List[str] = None
    ):
        """Mark a task as completed."""
        # Move from in_progress to completed
        for task in self.project_status["tasks_in_progress"]:
            if task["name"] == task_name:
                task["status"] = "completed"
                task["completed_at"] = datetime.utcnow().isoformat()
                task["summary"] = summary
                task["files_created"] = files_created or []
                task["files_modified"] = files_modified or []
                self.project_status["tasks_completed"].append(task)
                self.project_status["tasks_in_progress"].remove(task)
                
                await self.log_to_workspace(
                    agent_role=agent_role,
                    action=f"Completed task: {task_name}",
                    details=f"**Summary:** {summary}\n\n**Files Created:** {', '.join(files_created or [])}\n**Files Modified:** {', '.join(files_modified or [])}",
                    next_steps="Review and proceed to next task"
                )
                break
    
    async def coordinate_integration(self) -> Dict[str, Any]:
        """Coordinate the entire finance integration project."""
        logger.info("Starting finance integration coordination")
        
        # Create user context for agent coordination
        user_context = UserContext(
            user_id="finance_integration_project",
            emotional_state=EmotionalState.FOCUSED,
            activity_level=0.8
        )
        
        # Phase 1: Setup
        await self.log_to_workspace(
            agent_role=AgentRole.PROJECT_MANAGER.value,
            action="Project Kickoff",
            details="Starting finance integration project. Multi-agent coordinator setup complete.",
            next_steps="Begin backend integration phase"
        )
        
        # Define integration tasks
        integration_tasks = [
            {
                "name": "tocino-client",
                "agent": AgentRole.BACKEND_ENGINEER,
                "description": "Create Tocino API client with submit_ticket() and check_invoice_status() methods",
                "dependencies": []
            },
            {
                "name": "webhook-handler",
                "agent": AgentRole.BACKEND_ENGINEER,
                "description": "Create webhook handler with POST /ingress/tocino-webhook endpoint",
                "dependencies": ["tocino-client"]
            },
            {
                "name": "update-expense-handler",
                "agent": AgentRole.BACKEND_ENGINEER,
                "description": "Update expense handler to call Tocino API and store nova_request_id",
                "dependencies": ["tocino-client"]
            },
            {
                "name": "invoice-status-updater",
                "agent": AgentRole.BACKEND_ENGINEER,
                "description": "Create invoice status updater worker to poll Tocino",
                "dependencies": ["tocino-client"]
            },
            {
                "name": "admin-routes",
                "agent": AgentRole.FRONTEND_ENGINEER,
                "description": "Create admin routes with expense and invoice list views",
                "dependencies": []
            },
            {
                "name": "html-templates",
                "agent": AgentRole.FRONTEND_ENGINEER,
                "description": "Create HTML templates for dashboard, expenses, and invoices",
                "dependencies": ["admin-routes"]
            },
            {
                "name": "dashboard-integration",
                "agent": AgentRole.FRONTEND_ENGINEER,
                "description": "Integrate expense admin routes into Copa Telmex dashboard",
                "dependencies": ["admin-routes", "html-templates"]
            },
            {
                "name": "schema-alignment",
                "agent": AgentRole.DATABASE_ENGINEER,
                "description": "Review and align expense_reports and invoice_reports models",
                "dependencies": []
            },
            {
                "name": "migration-script",
                "agent": AgentRole.DATABASE_ENGINEER,
                "description": "Create migration script for schema alignment",
                "dependencies": ["schema-alignment"]
            }
        ]
        
        # Assign all tasks
        for task_def in integration_tasks:
            await self.assign_task(
                task_name=task_def["name"],
                agent_role=task_def["agent"],
                description=task_def["description"],
                dependencies=task_def["dependencies"]
            )
        
        return {
            "status": "coordinated",
            "tasks_assigned": len(integration_tasks),
            "project_status": self.project_status
        }
    
    async def get_project_status(self) -> Dict[str, Any]:
        """Get current project status."""
        return {
            "phase": self.project_status["phase"],
            "tasks_completed": len(self.project_status["tasks_completed"]),
            "tasks_in_progress": len(self.project_status["tasks_in_progress"]),
            "tasks_pending": len(self.project_status["tasks_pending"]),
            "issues": len(self.project_status["issues"]),
            "blockers": len(self.project_status["blockers"]),
            "details": self.project_status
        }


# Convenience function for agents to use
async def log_agent_work(
    agent_role: str,
    action: str,
    details: str,
    next_steps: Optional[str] = None,
    workspace_file: str = "/root/samchat/FINANCE_INTEGRATION_WORKSPACE.md"
):
    """Helper function for agents to log their work to workspace."""
    coordinator = FinanceIntegrationCoordinator(workspace_file)
    await coordinator.log_to_workspace(agent_role, action, details, next_steps)

