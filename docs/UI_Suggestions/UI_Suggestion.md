# QRadar Observability — Complete UI/UX Redesign & Customizable Dashboard Platform

You are acting as a Principal Product Designer, Senior Frontend Architect, and Enterprise Cybersecurity UX Engineer.

Your mission is to transform the existing QRadar-Dashboard application into a premium, production-grade Security Observability Platform.

**This is a comprehensive UI/UX modernization, not a rewrite of the existing product.**

The attached screenshot represents our desired Home Dashboard design and overall visual direction.

However, there is one mandatory modification:

**DO NOT copy the blue/navy background colors from the screenshot. The entire application must use a neutral charcoal/graphite dark theme.**

The objective is to combine:

- **CrowdStrike Falcon:** Professional security operations workflows, investigation interfaces, severity indicators, and high-density tables.
- **Datadog:** Operational observability, infrastructure visibility, meaningful metrics, and clean information architecture.
- **Grafana:** Advanced visualizations, customizable dashboards, time-series analysis, and flexible widget management.

The finished application must feel like a mature enterprise cybersecurity product, not an AI-generated dashboard template.

---

# 1. Repository Analysis — Mandatory First Step

Before making changes, thoroughly inspect the repository.

Identify:

- Current frontend framework and architecture.
- All existing pages and routes.
- Shared components and design patterns.
- Existing charting libraries.
- Current dashboard implementation.
- Backend APIs and data contracts.
- Authentication and authorization mechanisms.
- Existing user preferences and persistence.
- Current tests and quality checks.
- Product UX guidelines and architectural documentation.

Pay particular attention to:

- Log source monitoring.
- Behavioral anomaly detection.
- Baseline calculations.
- Investigation workflows.
- Evidence completeness.
- Detection coverage.
- Offense and rule monitoring.
- QRadar administration and operational health.

Read all relevant project documentation before designing or implementing changes.

Preserve existing product behavior and domain-specific requirements.

Do not assume functionality based solely on the reference screenshot.

---

# 2. Mandatory Visual Design System

## Neutral Charcoal / Graphite Dark Theme

The application must use a neutral dark color system.

**Absolutely no blue-tinted or purple-tinted application backgrounds.**

Use the following foundation:

| Token | Color |
|---|---|
| Application Background | #111214 |
| Sidebar Background | #151618 |
| Primary Surface | #1B1C1F |
| Elevated Surface | #252629 |
| Hover Surface | #2D2E32 |
| Border | #35363A |
| Primary Text | #E8E8E8 |
| Secondary Text | #96989D |
| Muted Text | #73757B |

Semantic colors:

| State | Color |
|---|---|
| Healthy / Success | #3FBF91 |
| Warning | #E5AD56 |
| Critical / Error | #E36565 |
| Neutral | #B6B8BD |

Design principles:

1. No navy backgrounds.
2. No purple gradients.
3. No blue-glowing cards.
4. No excessive neon effects.
5. No glassmorphism.
6. No oversized KPI cards.
7. No unnecessary decorative elements.
8. No inconsistent border radii.
9. No excessive whitespace that reduces operational visibility.
10. No generic AI dashboard aesthetics.

Use neutral gray for primary navigation, selected items, standard buttons, and ordinary interface controls.

Reserve semantic colors for actual operational meaning.

Charts may use additional distinguishable colors where necessary, but avoid making blue the dominant visual identity.

## Typography

Use a professional, highly readable typography system.

Prefer Inter or an equivalent modern sans-serif.

Use tabular numerals for metrics, timestamps, EPS values, and numeric tables.

Establish consistent typography tokens for:

- Page titles.
- Section headings.
- Widget titles.
- Table headers.
- Body text.
- Metadata.
- Numeric metrics.
- Status indicators.

Avoid unnecessarily small text.

## Spacing and Layout

Create a consistent spacing system.

Use:

- Compact enterprise layouts.
- Clear information hierarchy.
- Subtle surface separation.
- Consistent widget padding.
- Consistent chart margins.
- Well-aligned headers and controls.
- Predictable responsive behavior.

The interface should support dense operational information without feeling cluttered.

---

# 3. Application Shell and Navigation

Redesign the shared application shell.

Include:

- Persistent left sidebar.
- Application logo and identity.
- Organized navigation groups.
- Active page indicators.
- Global search where supported.
- Global time range controls where applicable.
- User profile and settings.
- System connection status.
- Contextual page actions.

Sidebar selection must use subtle neutral styling rather than bright blue backgrounds.

Use a slightly elevated charcoal surface and a restrained neutral selection indicator.

Preserve all existing navigation routes.

Do not remove or rename existing workflows without a clear migration strategy.

---

# 4. Home Dashboard — Primary Visual Reference

The attached screenshot is the primary layout and composition reference for the Home Dashboard.

Recreate its overall structure, visual hierarchy, information density, and professional appearance.

Replace its blue/navy color scheme with the mandatory charcoal design system.

The Home Dashboard should provide immediate operational awareness.

Recommended default widgets:

### KPI Overview

- Total EPS.
- Active Log Sources.
- Log Source Health.
- Active Anomalies.
- Active Offenses, if supported by existing APIs.

### Observed vs Expected EPS

A prominent time-series chart displaying:

- Actual observed EPS.
- Expected baseline.
- Expected range or confidence band when available.
- Anomaly markers.
- Interactive time selection.
- Tooltips with meaningful values.
- Drill-down to source-level investigation.

Never invent baseline confidence values.

### Top Anomalous Log Sources

A compact operational table showing available fields such as:

- Log source name.
- Source type.
- Observed EPS.
- Expected EPS.
- Deviation percentage.
- Anomaly severity.
- Last detection time.
- Investigation action.

Use actual available backend fields.

### Log Source Health

Display:

- Healthy sources.
- Silent sources.
- Degraded sources.
- Newly discovered sources.
- Sources exceeding expected volume.

Only display categories that are supported by actual detection logic.

### World Map / Event Geography

**Keep the world map. It is an intentional part of the design.**

The map should:

- Match the charcoal visual system.
- Use subtle geographic boundaries.
- Display meaningful event distribution.
- Support geographic filtering and drill-down when data is available.
- Avoid unnecessary animations.
- Remain available in the widget library.

Do not fabricate geographic attribution.

If geolocation data is unavailable, display a proper no-data state.

### Recent Anomalies

Display recent actionable anomalies with:

- Severity.
- Source.
- Anomaly type.
- Detection time.
- Status.
- Investigation link.

The Home Dashboard must provide a balanced overview rather than attempting to display every available metric simultaneously.

---

# 5. Redesign Every Existing Application Page

The visual design system must be applied across the entire application.

Discover all actual routes before implementation.

Do not restrict the redesign to the Home page.

Every page must feel like part of the same product.

However, do not copy the Home Dashboard layout onto every page.

Each page requires a purpose-built interface.

## Log Sources

Create an enterprise-grade source inventory and monitoring interface.

Prioritize:

- Dense, readable tables.
- Search and advanced filtering.
- Source health indicators.
- Source type classification.
- EPS and event volume.
- Last event received.
- Source-specific actions.
- Bulk operations where supported.

## Source Detail

Create a deep observability workspace.

Include existing supported functionality such as:

- Source metadata.
- EPS history.
- Observed vs expected volume.
- Baseline visualization.
- Historical anomaly timeline.
- Source health.
- Investigation navigation.
- Relevant evidence.

## Anomaly Investigation

Use a CrowdStrike-inspired investigation layout.

Prioritize:

- Clear anomaly identity.
- Severity and status.
- Investigation timeline.
- Evidence and supporting metrics.
- Baseline comparison.
- Source context.
- Detection confidence.
- Evidence completeness.
- Drill-down capabilities.

Preserve all existing investigation semantics and UX invariants.

Missing data must never be represented as zero.

## Offenses and Rules

Use a professional SOC operations layout.

Include existing supported capabilities such as:

- Operational tables.
- Severity and magnitude indicators.
- Search and filtering.
- Rule health.
- Status tracking.
- Detail views.
- Investigation navigation.

## Detection Coverage

Create an analytical interface for:

- Detection coverage.
- Detection health.
- Existing rule visibility.
- Coverage gaps.
- Relevant detection categories.

Do not introduce unsupported coverage claims.

## Administration and Settings

Use a structured enterprise configuration layout.

Prioritize:

- Clear forms.
- Configuration sections.
- Validation.
- Connection health.
- User preferences.
- Role-based access.
- Safe administrative actions.

Apply the same design language to all remaining existing routes, dialogs, settings panels, tables, and workflows.

---

# 6. Customizable Widget Dashboard Architecture

Implement a reusable widget-based dashboard system inspired by Grafana and Datadog.

Users should be able to create and customize dashboards without modifying code.

## Widget Library

Provide a widget catalog.

Initial widget candidates:

- Total EPS.
- Observed vs Expected EPS.
- Log Source Health.
- Top Anomalous Sources.
- Anomaly Severity Distribution.
- World Map.
- Recent Offenses.
- WAF Endpoint Traffic.
- IPS Source IP Anomalies.
- Ingestion Health.
- Source Classification Distribution.
- Detection Health.

Only activate widgets supported by existing data sources.

Clearly identify widgets requiring future backend capabilities.

## Widget Interactions

Support:

1. Add widget.
2. Remove widget.
3. Drag and drop.
4. Resize.
5. Widget configuration.
6. Widget-specific filters.
7. Widget-specific time range overrides.
8. Restore default configuration.
9. Expand widget for detailed analysis.
10. Navigate from widgets to relevant detail pages.

Use a responsive grid architecture.

Evaluate an established library such as react-grid-layout if appropriate for the existing frontend stack.

Avoid building a custom drag-and-drop engine unless necessary.

## Custom Dashboards

Support:

- Create dashboard.
- Rename dashboard.
- Duplicate dashboard.
- Delete dashboard.
- Save layout.
- Restore layout.
- Set default dashboard.
- Switch between dashboards.

Suggested default dashboard templates:

- SOC Overview.
- QRadar Administration.
- Behavioral Analytics.
- Log Source Monitoring.
- WAF / IPS Monitoring.

Templates must reflect actual available capabilities.

## Dashboard Persistence

Persist:

- Dashboard identity.
- Owner and access permissions.
- Widget types.
- Widget positions.
- Widget dimensions.
- Widget configurations.
- Time range preferences.
- Dashboard metadata.

Use existing persistence infrastructure where practical.

Do not introduce a new database without architectural justification.

Preserve authorization boundaries.

## Widget States

Every widget must support:

- Loading.
- Success.
- Empty.
- No Data.
- Stale Data.
- Error.
- Insufficient Permissions.

Never display fabricated metrics or placeholder operational data in production.

---

# 7. Charting and Data Visualization

Reuse the existing charting stack wherever practical.

Prioritize:

- Interactive time-series charts.
- Baseline comparisons.
- Confidence bands.
- Anomaly markers.
- Threshold indicators.
- Time range filtering.
- Drill-down interactions.
- Meaningful tooltips.
- Consistent legends.
- Accessible chart colors.

Charts must use the shared design tokens.

Avoid unnecessary chart decoration.

Prioritize analytical clarity over visual complexity.

---

# 8. UX and Interaction Quality

Every interactive component must behave consistently.

Standardize:

- Buttons.
- Dropdowns.
- Search inputs.
- Filters.
- Date selectors.
- Tables.
- Pagination.
- Sorting.
- Modals.
- Drawers.
- Tabs.
- Tooltips.
- Notifications.
- Empty states.
- Error messages.
- Confirmation dialogs.

Support keyboard navigation and accessible focus states.

Provide clear feedback for user actions.

Maintain readable layouts on supported screen sizes.

Optimize the primary desktop experience for SOC analysts and QRadar administrators.

---

# 9. Engineering and Architectural Requirements

This redesign must not compromise existing functionality.

Mandatory constraints:

- Preserve existing backend behavior.
- Preserve API contracts unless a change is necessary and documented.
- Preserve authentication and RBAC.
- Preserve anomaly detection algorithms.
- Preserve baseline calculations.
- Preserve source classification behavior.
- Preserve evidence completeness semantics.
- Preserve existing filters and workflows.
- Preserve all relevant UX invariants.
- Preserve test coverage.

Do not replace existing infrastructure simply because an alternative is newer.

Use reusable components and centralized design tokens.

Avoid duplicated styling and unnecessary dependencies.

Keep chart rendering and large tables performant.

Use efficient data fetching and caching strategies compatible with the existing architecture.

---

# 10. Implementation Strategy

Do not attempt to rewrite the entire frontend in one large change.

Work incrementally.

## Phase 0 — Repository Assessment

Inspect the repository and document:

- Existing architecture.
- Current pages.
- Available data sources.
- Current design system.
- UX constraints.
- Test coverage.
- Migration risks.

## Phase 1 — Design Foundation

Implement:

- Centralized theme tokens.
- Typography.
- Shared application shell.
- Sidebar.
- Navigation.
- Common UI components.
- Consistent tables and controls.

## Phase 2 — Home Dashboard

Rebuild the Home Dashboard according to the attached reference screenshot.

Apply the mandatory charcoal theme.

Integrate actual application data.

Preserve the world map.

## Phase 3 — Widget System

Implement:

- Widget registry.
- Widget library.
- Responsive grid.
- Drag and drop.
- Resize.
- Widget configuration.
- Dashboard customization.

## Phase 4 — Dashboard Persistence

Implement:

- Saved layouts.
- Multiple dashboards.
- Dashboard templates.
- User preferences.
- Authorization-aware persistence.

## Phase 5 — Remaining Pages

Redesign every remaining application page using the shared design system.

Preserve all existing features and workflows.

## Phase 6 — Quality Assurance

Validate:

- Visual consistency.
- Responsive behavior.
- Functional regression.
- Data correctness.
- Accessibility.
- Performance.
- Navigation.
- Dashboard persistence.
- Widget interactions.
- Existing tests.

Where browser automation or screenshot tooling is available, compare implemented pages against the design reference.

Do not claim visual validation unless it was actually performed.

---

# 11. Visual Quality Standards

The final interface must feel deliberately designed.

Avoid the following common AI-generated UI problems:

- Random gradients.
- Blue/purple color dominance.
- Excessive rounded corners.
- Oversized empty cards.
- Inconsistent typography.
- Misaligned elements.
- Decorative charts with no operational purpose.
- Inconsistent spacing.
- Too many competing accent colors.
- Unnecessary animations.
- Different visual styles across pages.

Aim for a premium, restrained, highly functional enterprise application.

The UI should remain visually attractive while supporting real operational workloads.

---

# 12. Final Acceptance Criteria

The redesign is successful only when:

1. The Home Dashboard closely follows the provided visual reference, using neutral charcoal instead of navy.
2. Every existing page uses the same cohesive design system.
3. All existing functionality remains operational.
4. Users can add, remove, resize and rearrange dashboard widgets.
5. Dashboard layouts persist correctly.
6. The world map remains available.
7. Widgets use real data and handle missing data correctly.
8. Investigation workflows retain their existing semantics.
9. Navigation and responsive behavior are consistent.
10. Relevant tests pass and regressions are addressed.

---

# Execution Instructions

Start by inspecting the repository.

Create a detailed implementation plan in:

`docs/UI-REDESIGN-PLAN.md`

Include a complete page inventory, design tokens, component architecture, widget architecture, implementation phases, and risk assessment.

Then present a concise summary of your findings and proposed implementation sequence.

**Do not modify application code until I approve the plan.**

Once approved, implement one phase at a time.

After each phase:

- Run relevant tests.
- Check existing functionality.
- Verify design consistency.
- Report changes and remaining issues.
- Wait for approval before proceeding.

Do not sacrifice functional correctness for visual appearance.

The final product should combine the security workflow quality of CrowdStrike, the observability experience of Datadog, and the dashboard flexibility of Grafana — with its own distinctive neutral charcoal visual identity.