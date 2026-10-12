"""Reject unreadable scientific-sleep targets before any selected-day writes."""
from .model import check, validate


def validate_sleep_upload(plan, source):
    sleep = plan.get('sleep')
    if not sleep:
        return
    registered = source.get('sleepSource') or {}
    code, category = str(registered.get('deviceCode', '')), registered.get('deviceCategory')
    check(source.get('resultCode') == 0 and registered.get('sourceKind') == 'registered_wearable' and
          code.isdigit() and 0 < int(code) < 2**63 and type(category) is int and category > 0,
          'Registered sleep device unavailable or ambiguous')
    check(all(str(r.get('deviceCode')) == code for r in sleep['target']),
          'Sleep target is not the registered wearable; create and review a new plan')
    total = sleep['summary']
    check(total.get('recordDay') == int(plan['config']['date'].replace('-', '')) and
          total.get('dataSource') == 2 and str(total.get('deviceCode')) == '0' and
          total.get('timeZone') == plan['config']['timezone'].replace(':', ''), 'Sleep summary request scope invalid')
    fields = total.get('professionalSleep') or {}
    check(fields.get('deviceCategory') == category, 'Sleep summary wearable category missing or mismatched; create a new plan')
    validate(sleep['target'], sleep['target_start'], sleep['target_end'])
    keys = [r['samplePoints'][0]['key'] for r in sleep['target']]
    check(fields.get('allSleepTime') == len(keys) and
          all(fields.get(field) == keys.count(key) for field, key in [
              ('lightSleepTime', 'PROFESSIONAL_SLEEP_SHALLOW'), ('deepSleepTime', 'PROFESSIONAL_SLEEP_DEEP'),
              ('dreamTime', 'PROFESSIONAL_SLEEP_DREAM')]) and
          str(fields.get('fallAsleepTime')) == str(sleep['target_start']) and
          str(fields.get('wakeupTime')) == str(sleep['target_end']), 'Sleep summary disagrees with minute timeline')
    score = fields.get('sleepScore')
    check(type(score) is int and 0 <= score <= 100, 'Sleep score out of range')
    if (plan.get('request', {}).get('sleep') or {}).get('restore_original_score'):
        check(score > 0, 'Original score missing or invalid; backup a positive score before phone deletion')


def registered_sleep_source(client, plan):
    codes = {str(r['deviceCode']) for r in plan['sleep']['target']}
    from .standalone import SOURCE
    check(len(codes) == 1, 'Multiple sleep sources require an explicit reviewed plan')
    values = {'upload_require_sleep_source': True}
    if SOURCE not in codes:
        values['sleep_upload_device_code'] = next(iter(codes))
    client.configure(**values)
    return client.upload_source()
