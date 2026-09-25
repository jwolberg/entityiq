/** Test fixture: a MATCH run's explanation (tickets 0069, 0070). */
import type { ScreeningExplanation } from "../api/client";

export const EXPLANATION: ScreeningExplanation = {
  run_id: "r1",
  decision_id: "d1",
  decided_at: "2026-09-25T10:00:00+00:00",
  rule_version: 1,
  normalizer_version: "n2",
  subject_shredded: false,
  steps: [
    {
      kind: "sources",
      max_age_days: 7,
      lists: [
        {
          source: "ofac_sdn", display_name: "OFAC SDN List", required: true, status: "complete",
          snapshot_id: "s1", retrieved_at: "2026-09-20T06:00:00+00:00",
          content_sha256: "abcdef0123456789abcdef", record_count: 7534,
          source_url: "https://www.treasury.gov/ofac/downloads/sdn.csv",
        },
        {
          source: "uk_ofsi", display_name: "UK OFSI Consolidated List", required: true,
          status: "unavailable", snapshot_id: null, retrieved_at: null, content_sha256: null,
          record_count: null, source_url: null,
        },
      ],
    },
    {
      kind: "blocking",
      candidate_count: 2,
      cap: 200,
      candidates: [
        { candidate_id: "c1", matched_keys: ["mp:TFTR"], record_ref: {
          source: "ofac_sdn", display_name: "OFAC SDN List", entry_id: "1234", snapshot_id: "s1",
          primary_name: "Teodor Vasilescu", program: "SDGT" } },
        { candidate_id: "c2", matched_keys: ["sk:vslsk"], record_ref: {
          source: "ofac_sdn", display_name: "OFAC SDN List", entry_id: "5678", snapshot_id: "s1",
          primary_name: "Tudor Vasilescou", program: null } },
      ],
    },
    {
      kind: "scoring",
      candidates: [
        {
          candidate_id: "c1", score: 0.9,
          record_ref: { source: "ofac_sdn", display_name: "OFAC SDN List", entry_id: "1234",
            snapshot_id: "s1", primary_name: "Teodor Vasilescu", program: "SDGT" },
          terms: [
            { name: "name_exact_normalized", label: "Name matches exactly (after normalization)",
              weight: 0.5, direction: "+", record_field: "names", record_value: "Teodor Vasilescu",
              citations: ["sub-names", "rec-names"] },
            { name: "dob_conflict", label: "Full dates of birth differ", weight: -0.35,
              direction: "-", record_field: "dobs", record_value: [{ date: "1962-08-31" }],
              citations: ["sub-dob", "rec-dobs"] },
          ],
        },
        {
          candidate_id: "c2", score: 0.2,
          record_ref: { source: "ofac_sdn", display_name: "OFAC SDN List", entry_id: "5678",
            snapshot_id: "s1", primary_name: "Tudor Vasilescou", program: null },
          terms: [],
        },
      ],
    },
    {
      kind: "banding",
      thresholds: { clear_below: 0.35, match_at: 0.9 },
      candidates: [
        { candidate_id: "c1", score: 0.9, band: "MATCH", reason_code: "score_at_or_above_match",
          reason_text: "Score 0.9 is at or above the match threshold (0.9) → MATCH.", consistent: true },
        { candidate_id: "c2", score: 0.2, band: "CLEAR", reason_code: "score_below_clear",
          reason_text: "Score 0.2 is below the clear threshold (0.35) → CLEAR.", consistent: true },
      ],
    },
    {
      kind: "disposition",
      system_disposition: "MATCH",
      auto_closed: false,
      reason_code: "rollup_most_severe",
      reason_text: "The run takes its most severe candidate band: MATCH.",
      coverage_gaps: ["list:uk_ofsi"],
      common_name: null,
      consistent: true,
    },
    {
      kind: "human",
      dispositions: [
        { disposition: "MATCH", created_at: "2026-09-25T11:00:00+00:00", notes: "confirmed on passport" },
      ],
      replays: [{ reproduced: true, shredded: false, occurred_at: "2026-09-25T12:00:00+00:00" }],
    },
  ],
  citations: {
    "sub-names": { about: "subject", source: "subject_submission", field: "name", submitted_at: null },
    "sub-dob": { about: "subject", source: "subject_submission", field: "dob", submitted_at: null },
    "rec-names": {
      about: "record", source: "ofac_sdn", display_name: "OFAC SDN List", entry_id: "1234",
      field: "names", snapshot_id: "s1", snapshot_retrieved_at: "2026-09-20T06:00:00+00:00",
      content_sha256: "abcdef0123456789abcdef", locator: "ofac_sdn:1234@s1#names",
      source_url: "https://www.treasury.gov/ofac/downloads/sdn.csv",
    },
    "rec-dobs": {
      about: "record", source: "ofac_sdn", display_name: "OFAC SDN List", entry_id: "1234",
      field: "dobs", snapshot_id: "s1", snapshot_retrieved_at: "2026-09-20T06:00:00+00:00",
      content_sha256: "abcdef0123456789abcdef", locator: "ofac_sdn:1234@s1#dobs",
      source_url: "https://www.treasury.gov/ofac/downloads/sdn.csv",
    },
  },
};
