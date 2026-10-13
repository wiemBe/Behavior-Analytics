// The subject filters are parsed in `lib/anomalyQuery.ts`, beside Phase A's parser,
// so there is one parser. Re-exported here for the callers that name this path.

export {
  SUBJECT_KINDS,
  parseSubjectQuery,
  subjectKindLabel,
  type SubjectQuery,
} from "@/lib/anomalyQuery";
