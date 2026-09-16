// Labels describe the selected/executed algorithm, never an assumed release version.
export function policyProfile(policy) {
  const bios7 = policy === 'BIOS_PIBT.7';
  const bios6 = policy === 'BIOS_PIBT.6';
  const version = bios7 ? '7.0' : bios6 ? '6.0' : null;
  return {
    title: version ? `BIOS ${version}` : String(policy || 'BIOS').replaceAll('_', ' '),
    predictive: bios6 || bios7,
    passageRelease: bios7,
    mode: bios7 ? 'CORRIDOR RELEASE + PREDICTION' : bios6 ? 'PREDICTIVE EDGE' : 'REFERENCE POLICY',
  };
}
