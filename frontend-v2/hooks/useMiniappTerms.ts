"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  acceptMiniappTerms,
  fetchMiniappTermsStatus,
  type AcceptMiniappTermsRequest,
  type MiniappTermsStatusResponse,
} from "@/lib/api";

type PendingTermsAction = (() => void) | null;

export function useMiniappTerms() {
  const [termsStatus, setTermsStatus] = useState<MiniappTermsStatusResponse | null>(null);
  const [termsModalOpen, setTermsModalOpen] = useState(false);
  const [termsAccepting, setTermsAccepting] = useState(false);
  const [termsError, setTermsError] = useState<string | null>(null);
  const pendingActionRef = useRef<PendingTermsAction>(null);

  const termsEnabled = termsStatus?.enabled === true;
  const termsAccepted = !termsEnabled || Boolean(termsStatus?.accepted && !termsStatus.requires_acceptance);
  const termsVersion = termsStatus?.version || "miniapp-risk-v1-2026-05";
  const termsAcceptedRef = useRef(true);
  const termsEnabledRef = useRef(false);

  useEffect(() => {
    termsAcceptedRef.current = termsAccepted;
    termsEnabledRef.current = termsEnabled;
  }, [termsAccepted, termsEnabled]);

  useEffect(() => {
    let cancelled = false;
    fetchMiniappTermsStatus()
      .then((status) => {
        if (cancelled) return;
        setTermsStatus(status);
        if (status.enabled === false) {
          termsAcceptedRef.current = true;
          termsEnabledRef.current = false;
          pendingActionRef.current = null;
          setTermsModalOpen(false);
          return;
        }
        termsEnabledRef.current = true;
        if (status.requires_acceptance) {
          setTermsModalOpen(true);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        termsAcceptedRef.current = true;
        termsEnabledRef.current = false;
        pendingActionRef.current = null;
        setTermsError(error instanceof Error ? error.message : "Chưa tải được điều khoản.");
        setTermsModalOpen(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const requireTerms = useCallback(
    (afterAccept?: () => void): boolean => {
      if (!termsEnabledRef.current) {
        return false;
      }
      if (termsAcceptedRef.current) {
        return false;
      }
      pendingActionRef.current = afterAccept ?? null;
      setTermsError(null);
      setTermsModalOpen(true);
      return true;
    },
    []
  );

  const acceptTerms = useCallback(async (payload: AcceptMiniappTermsRequest) => {
    setTermsAccepting(true);
    setTermsError(null);
    try {
      const nextStatus = await acceptMiniappTerms(payload);
      termsAcceptedRef.current = Boolean(nextStatus.accepted && !nextStatus.requires_acceptance);
      termsEnabledRef.current = nextStatus.enabled !== false;
      setTermsStatus(nextStatus);
      setTermsModalOpen(false);
      const pending = pendingActionRef.current;
      pendingActionRef.current = null;
      if (pending) {
        window.setTimeout(pending, 0);
      }
    } catch (error) {
      setTermsError(error instanceof Error ? error.message : "Chưa lưu được xác nhận điều khoản.");
    } finally {
      setTermsAccepting(false);
    }
  }, []);

  return {
    termsAccepted,
    termsEnabled,
    termsVersion,
    termsModalOpen,
    termsAccepting,
    termsError,
    requireTerms,
    acceptTerms,
  };
}
