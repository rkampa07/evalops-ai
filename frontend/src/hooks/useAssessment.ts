import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  AssessmentCreate,
  AssessmentJob,
  AssessmentResult,
  AssessmentStatus,
} from "../api/types";

const DEFAULT_FIRST_POLL_MS = 1000;
const DEFAULT_MAX_POLL_MS = 5000;

export interface UseAssessmentOptions {
  firstPollMs?: number;
  maxPollMs?: number;
}

export interface SubmitAssessmentOptions {
  forceNewKey?: boolean;
}

export interface UseAssessmentState {
  job: AssessmentJob | null;
  result: AssessmentResult | null;
  status: AssessmentStatus | null;
  submitting: boolean;
  cancelling: boolean;
  loadingResult: boolean;
  isActive: boolean;
  isTerminal: boolean;
  error: string | null;
  pollError: string | null;
  idempotencyKey: string | null;
  submit: (
    request: AssessmentCreate,
    options?: SubmitAssessmentOptions,
  ) => Promise<AssessmentJob | null>;
  cancel: () => Promise<AssessmentJob | null>;
  reset: () => void;
}

interface PendingAttempt {
  fingerprint: string;
  key: string;
  assessmentId: number | null;
}

function generateIdempotencyKey(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `assess-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function makeFingerprint(runId: number, resultId: number, request: AssessmentCreate): string {
  if (request.evaluator === "citation") {
    return JSON.stringify({
      run_id: runId,
      result_id: resultId,
      evaluator: "citation",
      citation_expected: Boolean(request.citation_expected),
    });
  }
  return JSON.stringify({
    run_id: runId,
    result_id: resultId,
    evaluator: "claim",
  });
}

export function isTerminalAssessmentStatus(
  status: AssessmentStatus | null | undefined,
): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

function isRetryableResultError(cause: unknown): boolean {
  if (cause instanceof ApiError) {
    return cause.status === 409 || cause.status === 0 || cause.status >= 500;
  }
  return true;
}

/**
 * Manages the full lifecycle of a frozen citation or claim assessment for one saved result:
 * idempotent submission, status polling, result retrieval on completion, cancellation, and cleanup.
 */
export function useAssessment(
  runId: number,
  resultId: number,
  options: UseAssessmentOptions = {},
): UseAssessmentState {
  const firstPollMs = options.firstPollMs ?? DEFAULT_FIRST_POLL_MS;
  const maxPollMs = options.maxPollMs ?? DEFAULT_MAX_POLL_MS;

  const [job, setJob] = useState<AssessmentJob | null>(null);
  const [result, setResult] = useState<AssessmentResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [loadingResult, setLoadingResult] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const [pollRevision, setPollRevision] = useState(0);

  const submittingRef = useRef(false);
  const pendingAttemptRef = useRef<PendingAttempt | null>(null);
  const sequenceRef = useRef(0);
  const pollControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const targetRef = useRef({ runId, resultId });

  const abortActivePoll = useCallback(() => {
    sequenceRef.current += 1;
    if (pollControllerRef.current) {
      pollControllerRef.current.abort();
      pollControllerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortActivePoll();
    };
  }, [abortActivePoll]);

  useEffect(() => {
    if (targetRef.current.runId === runId && targetRef.current.resultId === resultId) {
      return;
    }
    targetRef.current = { runId, resultId };
    abortActivePoll();
    submittingRef.current = false;
    pendingAttemptRef.current = null;
    jobRef.current = null;
    setJob(null);
    setResult(null);
    setSubmitting(false);
    setCancelling(false);
    setLoadingResult(false);
    setError(null);
    setPollError(null);
    setIdempotencyKey(null);
  }, [abortActivePoll, resultId, runId]);

  const assessmentId = job?.assessment_id ?? null;
  const jobStatus = job?.status ?? null;
  const jobRef = useRef<AssessmentJob | null>(job);
  jobRef.current = job;

  const needsPolling =
    assessmentId !== null &&
    (jobStatus === "queued" ||
      jobStatus === "running" ||
      (jobStatus === "succeeded" && result === null && error === null));

  useEffect(() => {
    if (!needsPolling || assessmentId === null) return;

    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = firstPollMs;
    const controller = new AbortController();
    pollControllerRef.current = controller;

    const pollStep = async () => {
      const seq = ++sequenceRef.current;
      try {
        let currentStatus = jobRef.current?.status ?? null;
        if (currentStatus !== "succeeded") {
          const latestJob = await api.getAssessment(assessmentId, controller.signal);
          if (stopped || !mountedRef.current || seq !== sequenceRef.current) return;
          jobRef.current = latestJob;
          setJob(latestJob);
          setPollError(null);
          currentStatus = latestJob.status;

          if (currentStatus === "failed") {
            setError(latestJob.error?.message ?? "Assessment failed.");
            return;
          }
          if (currentStatus === "cancelled") {
            return;
          }
        }

        if (currentStatus === "succeeded") {
          setLoadingResult(true);
          try {
            const loadedResult = await api.getAssessmentResult(assessmentId, controller.signal);
            if (stopped || !mountedRef.current || seq !== sequenceRef.current) return;
            setResult(loadedResult);
            setPollError(null);
            setLoadingResult(false);
            return;
          } catch (resultErr) {
            if (
              stopped ||
              !mountedRef.current ||
              controller.signal.aborted ||
              seq !== sequenceRef.current
            ) {
              return;
            }
            setLoadingResult(false);
            const message =
              resultErr instanceof Error
                ? resultErr.message
                : "Assessment result is not available yet.";
            if (!isRetryableResultError(resultErr)) {
              setError(message);
              return;
            }
            setPollError(message);
          }
        }
      } catch (pollErr) {
        if (
          stopped ||
          !mountedRef.current ||
          controller.signal.aborted ||
          seq !== sequenceRef.current
        ) {
          return;
        }
        setPollError(
          pollErr instanceof Error
            ? pollErr.message
            : "Assessment status is temporarily unavailable.",
        );
      }

      if (stopped || !mountedRef.current || controller.signal.aborted) return;
      timer = setTimeout(pollStep, delay);
      delay = Math.min(maxPollMs, Math.round(delay * 1.5));
    };

    void pollStep();

    return () => {
      stopped = true;
      controller.abort();
      if (pollControllerRef.current === controller) {
        pollControllerRef.current = null;
      }
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [assessmentId, firstPollMs, maxPollMs, needsPolling, pollRevision]);

  const submit = useCallback(
    async (
      request: AssessmentCreate,
      submitOptions?: SubmitAssessmentOptions,
    ): Promise<AssessmentJob | null> => {
      if (submittingRef.current) return null;
      if (job && (job.status === "queued" || job.status === "running")) return null;

      abortActivePoll();
      const fingerprint = makeFingerprint(runId, resultId, request);
      const pending = pendingAttemptRef.current;
      const shouldReuseKey =
        !submitOptions?.forceNewKey &&
        pending !== null &&
        pending.fingerprint === fingerprint &&
        pending.assessmentId === null;

      const key = shouldReuseKey ? pending.key : generateIdempotencyKey();
      pendingAttemptRef.current = {
        fingerprint,
        key,
        assessmentId: null,
      };

      submittingRef.current = true;
      jobRef.current = null;
      setIdempotencyKey(key);
      setSubmitting(true);
      setJob(null);
      setResult(null);
      setError(null);
      setPollError(null);
      setLoadingResult(false);

      const submitSeq = ++sequenceRef.current;
      try {
        const submitted = await api.createAssessment(runId, resultId, request, key);
        if (!mountedRef.current || submitSeq !== sequenceRef.current) return null;

        pendingAttemptRef.current = {
          fingerprint,
          key,
          assessmentId: submitted.assessment_id,
        };
        jobRef.current = submitted;
        setJob(submitted);
        if (submitted.status === "failed") {
          setError(submitted.error?.message ?? "Assessment failed.");
        }
        return submitted;
      } catch (submitErr) {
        if (!mountedRef.current || submitSeq !== sequenceRef.current) return null;
        if (submitErr instanceof ApiError && submitErr.status >= 400 && submitErr.status < 500) {
          // A deterministic 4xx rejection means the server rejected this request/key; do not pin a broken key.
          pendingAttemptRef.current = null;
        }
        setError(
          submitErr instanceof Error ? submitErr.message : "Could not submit assessment request.",
        );
        return null;
      } finally {
        if (mountedRef.current) {
          submittingRef.current = false;
          setSubmitting(false);
        }
      }
    },
    [abortActivePoll, job, resultId, runId],
  );

  const cancel = useCallback(async (): Promise<AssessmentJob | null> => {
    if (!job || (job.status !== "queued" && job.status !== "running") || cancelling) {
      return null;
    }
    abortActivePoll();
    const cancelSeq = ++sequenceRef.current;
    setCancelling(true);
    setPollError(null);
    try {
      const updated = await api.cancelAssessment(job.assessment_id);
      if (!mountedRef.current || cancelSeq !== sequenceRef.current) return null;
      jobRef.current = updated;
      setJob(updated);
      return updated;
    } catch (cancelErr) {
      if (!mountedRef.current || cancelSeq !== sequenceRef.current) return null;
      if (!(cancelErr instanceof ApiError && cancelErr.status === 409)) {
        setPollError(
          cancelErr instanceof Error ? cancelErr.message : "Cancellation could not be confirmed.",
        );
      }
      return null;
    } finally {
      if (mountedRef.current) {
        setCancelling(false);
        setPollRevision((rev) => rev + 1);
      }
    }
  }, [abortActivePoll, cancelling, job]);

  const reset = useCallback(() => {
    abortActivePoll();
    submittingRef.current = false;
    pendingAttemptRef.current = null;
    jobRef.current = null;
    setJob(null);
    setResult(null);
    setSubmitting(false);
    setCancelling(false);
    setLoadingResult(false);
    setError(null);
    setPollError(null);
    setIdempotencyKey(null);
  }, [abortActivePoll]);

  const status = job?.status ?? null;
  const isTerminal = isTerminalAssessmentStatus(status);
  const isActive =
    submitting ||
    status === "queued" ||
    status === "running" ||
    (status === "succeeded" && result === null && error === null);

  return {
    job,
    result,
    status,
    submitting,
    cancelling,
    loadingResult,
    isActive,
    isTerminal,
    error,
    pollError,
    idempotencyKey,
    submit,
    cancel,
    reset,
  };
}
