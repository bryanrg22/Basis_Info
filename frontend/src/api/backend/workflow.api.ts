/**
 * Backend Workflow API Client
 *
 * Client for calling the FastAPI backend workflow endpoints.
 * Triggers and manages the LangGraph agentic workflow.
 */

import { API_CONFIG } from '@/config/env';

// =============================================================================
// Types
// =============================================================================

export interface StartWorkflowRequest {
  study_id: string;
  reference_doc_ids?: string[];
  study_doc_ids?: string[];
}

export interface ResumeWorkflowRequest {
  study_id: string;
  engineer_approved?: boolean;
  corrections?: Record<string, unknown>[];
}

export interface TriggerStageRequest {
  study_id: string;
  stage: string;
  reference_doc_ids?: string[];
  study_doc_ids?: string[];
}

export interface WorkflowResponse {
  study_id: string;
  status: 'running' | 'paused' | 'completed' | 'error';
  current_stage: string;
  needs_review: boolean;
  items_needing_review: string[];
  message: string;
}

export interface WorkflowStatusResponse {
  study_id: string;
  current_stage: string;
  rooms_count: number;
  objects_count: number;
  classifications_count: number;
  needs_review: boolean;
  items_needing_review: string[];
}

export interface WorkflowEvidenceResponse {
  study_id: string;
  total_citations: number;
  citations: Array<{
    component: string;
    doc_id: string;
    page?: number;
    text?: string;
    confidence?: number;
  }>;
}

// =============================================================================
// API Client
// =============================================================================

class WorkflowApiClient {
  private baseUrl: string;

  constructor() {
    this.baseUrl = API_CONFIG.backendUrl;
  }

  private async fetch<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;

    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
  }

  /**
   * Start a new workflow for a study
   */
  async startWorkflow(request: StartWorkflowRequest): Promise<WorkflowResponse> {
    return this.fetch<WorkflowResponse>('/workflow/start', {
      method: 'POST',
      body: JSON.stringify({
        study_id: request.study_id,
        reference_doc_ids: request.reference_doc_ids || [],
        study_doc_ids: request.study_doc_ids || [],
      }),
    });
  }

  /**
   * Resume a workflow after engineer review
   */
  async resumeWorkflow(request: ResumeWorkflowRequest): Promise<WorkflowResponse> {
    return this.fetch<WorkflowResponse>('/workflow/resume', {
      method: 'POST',
      body: JSON.stringify({
        study_id: request.study_id,
        engineer_approved: request.engineer_approved ?? true,
        corrections: request.corrections || [],
      }),
    });
  }

  /**
   * Trigger a specific workflow stage
   */
  async triggerStage(
    stage: string,
    request: TriggerStageRequest
  ): Promise<WorkflowResponse> {
    return this.fetch<WorkflowResponse>(`/workflow/stage/${stage}`, {
      method: 'POST',
      body: JSON.stringify({
        study_id: request.study_id,
        stage: request.stage,
        reference_doc_ids: request.reference_doc_ids || [],
        study_doc_ids: request.study_doc_ids || [],
      }),
    });
  }

  /**
   * Get workflow status for a study
   */
  async getStatus(studyId: string): Promise<WorkflowStatusResponse> {
    return this.fetch<WorkflowStatusResponse>(`/workflow/${studyId}/status`);
  }

  /**
   * Get evidence citations for a study
   */
  async getEvidence(studyId: string): Promise<WorkflowEvidenceResponse> {
    return this.fetch<WorkflowEvidenceResponse>(`/workflow/${studyId}/evidence`);
  }

  /**
   * Check if backend is healthy
   */
  async healthCheck(): Promise<{ status: string }> {
    return this.fetch<{ status: string }>('/health');
  }
}

// Export singleton instance
export const workflowApi = new WorkflowApiClient();
