import { MEASURE_SNAP_LABELS, formatMeasurementAngle } from "@text-to-cad/core/lib/viewer/measurement.js";
import { measureLabelText } from "@text-to-cad/core/lib/viewer/measureDimension.js";
import { CoordValue, InfoRow, MonoValue } from "../../../kit/inspector/referenceRows.jsx";

/**
 * A measurement's card, its Quick Edit chip's: the full reading — the distance as the dimension
 * labels it, the angle where two faces or edges make one, the delta along each axis, and what each
 * end bound to, so an exact edge or face reading is told from a free point off the surface. Rows
 * only, as every chip's card is: the card insets them.
 * @param {{ item: { pickA?: object, pickB?: object, measurement: object } }} props
 */
export default function MeasurementCard({ item }) {
  const angle = formatMeasurementAngle(item.measurement);
  const delta = item.measurement?.delta;
  const first = MEASURE_SNAP_LABELS[item.pickA?.snapKind];
  const second = MEASURE_SNAP_LABELS[item.pickB?.snapKind];
  return <>
    <InfoRow label="Distance"><MonoValue>{measureLabelText(item.measurement)}</MonoValue></InfoRow>
    {angle ? <InfoRow label="Angle"><MonoValue>{angle}</MonoValue></InfoRow> : null}
    {Array.isArray(delta) && delta.length >= 3 ? <InfoRow label="Delta"><CoordValue vector={delta} /></InfoRow> : null}
    {first && second ? <InfoRow label="Between" copy={false}>{`${first} → ${second}`}</InfoRow> : null}
  </>;
}
