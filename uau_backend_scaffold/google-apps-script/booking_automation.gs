function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);

    const parentName = payload.parent_name || "";
    const studentName = payload.student_name || "";
    const email = payload.email || "";
    const sessions = payload.sessions || [];
    const bookingGroupId = payload.booking_group_id || "";
    const orderId = payload.paypal_order_id || "";
    const captureId = payload.paypal_capture_id || "";

    const sessionLines = sessions.map(function(s) {
      return "- " + s.booking_date + " at " + s.booking_time + " (" + s.session_type + ") — $" + s.total_cost;
    }).join("\n");

    const total = sessions.reduce(function(sum, s) {
      return sum + Number(s.total_cost || 0);
    }, 0);

    const subject = "Invoice draft — Up And Up Chess Club — " + studentName;
    const body =
      "Parent: " + parentName + "\n" +
      "Student: " + studentName + "\n" +
      "Email: " + email + "\n" +
      "Booking Group ID: " + bookingGroupId + "\n" +
      "PayPal Order ID: " + orderId + "\n" +
      "PayPal Capture ID: " + captureId + "\n\n" +
      "Sessions:\n" + sessionLines + "\n\n" +
      "Total Paid: $" + total + "\n\n" +
      "Draft this into a final invoice email.";

    // Gmail draft in your own inbox
    GmailApp.createDraft(Session.getActiveUser().getEmail(), subject, body);

    // Calendar event on your primary calendar for the first session
    if (sessions.length > 0) {
      var first = sessions[0];
      var start = buildDateTime(first.booking_date, first.booking_time);
      var end = new Date(start.getTime() + 50 * 60 * 1000);

      CalendarApp.getDefaultCalendar().createEvent(
        "Chess Session — " + studentName,
        start,
        end,
        {
          description:
            "Parent: " + parentName + "\n" +
            "Email: " + email + "\n" +
            "Booking Group ID: " + bookingGroupId + "\n\n" +
            sessionLines,
          guests: email,
          sendInvites: true
        }
      );
    }

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function buildDateTime(dateStr, timeStr) {
  // Example inputs: 2026-04-21 and 4:00 PM
  var parts = dateStr.split("-");
  var date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));

  var match = timeStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!match) return date;

  var hour = Number(match[1]);
  var minute = Number(match[2]);
  var meridiem = match[3].toUpperCase();

  if (meridiem === "PM" && hour < 12) hour += 12;
  if (meridiem === "AM" && hour === 12) hour = 0;

  date.setHours(hour, minute, 0, 0);
  return date;
}
