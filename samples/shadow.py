# Copyright 2010-2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
#  http://aws.amazon.com/apache2.0
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

from __future__ import absolute_import
from __future__ import print_function
import argparse
from aws_crt import io, mqtt
from awsiot import iotshadow
from concurrent import futures
import threading

# - Overview -
# This sample uses the AWS IoT Device Shadow Service to keep a property in
# sync between device and server. Imagine a light whose color may be changed
# through an app, or set by a local user.
#
# - Instructions -
# Once connected, type a value in the terminal and press Enter to update
# the property's "reported" value. The sample also responds when the "desired"
# value changes on the server. To observe this, edit the Shadow document in
# the AWS Console and set a new "desired" value.
#
# - Detail -
# On startup, the sample requests the shadow document to learn the property's
# initial state. The sample also subscribes to "delta" events from the server,
# which are sent when a property's "desired" value differs from its "reported"
# value. When the sample learns of a new desired value, that value is changed
# on the device and an update is sent to the server with the new "reported"
# value.

parser = argparse.ArgumentParser(description="Device Shadow sample keeps a property in sync across client and server")
parser.add_argument('--endpoint', required=True, help="Your AWS IoT custom endpoint, not including a port. " +
                                                      "Ex: \"w6zbse3vjd5b4p-ats.iot.us-west-2.amazonaws.com\"")
parser.add_argument('--cert', required=True, help="File path to your client certificate, in PEM format")
parser.add_argument('--key', required=True, help="File path to your private key file, in PEM format")
parser.add_argument('--root-ca', help="File path to root certificate authority, in PEM format. " +
                                      "Necessary if MQTT server uses a certificate that's not already in " +
                                      "your trust store")
parser.add_argument('--client-id', default='samples-client-id', help="Client ID for MQTT connection.")
parser.add_argument('--thing-name', required=True, help="The name assigned to your IoT Thing")
parser.add_argument('--shadow-property', default="color", help="Name of property in shadow to keep in sync")

# Using globals to simplify sample code
connected_future = futures.Future()
disconnect_called = False
is_sample_done = threading.Event()

mqtt_connection = None
shadow_client = None
thing_name = ""
shadow_property = ""

# Acquire shadow_value_lock before touching shadow_value, which may be changed from any thread
shadow_value = None
shadow_value_lock = threading.Lock()
SHADOW_VALUE_DEFAULT = "off"

# Function for gracefully quitting this sample
def exit(msg):
    print("Exiting Sample:", msg)
    print("Disconnecting...")
    global disconnect_called
    disconnect_called = True
    mqtt_connection.disconnect()

def on_connected(return_code, session_present):
    # type: (int, bool) -> None
    print("Connect completed with code: {}".format(return_code))
    if return_code == 0:
        connected_future.set_result(None)
    else:
        connected_future.set_exception(RuntimeError("Connection failed with code: {}".format(return_code)))

def on_disconnected(return_code):
    # type: (int) -> bool
    print("Disconnected with code: {}".format(return_code))
    if disconnect_called:
        # Signal that sample is finished
        is_sample_done.set()
        # Don't attempt to reconnect
        return False
    else:
        # Attempt to reconnect
        return True

def on_get_accepted(response):
    # type: (iotshadow.GetShadowResponse) -> None
    try:
        print("Finished getting initial shadow state.")

        with shadow_value_lock:
            if shadow_value is not None:
                print("  Ignoring initial query because a delta event has already been received.")
                return

        if response.state:
            if response.state.delta:
                value = response.state.delta.get(shadow_property)
                if value:
                    print("  Shadow contains delta value '{}'.".format(value))
                    change_shadow_value(value)
                    return

            if response.state.reported:
                value = response.state.reported.get(shadow_property)
                if value:
                    print("  Shadow contains reported value '{}'.".format(value))
                    set_local_value_due_to_initial_query(response.state.reported[shadow_property])
                    return

        print("  Shadow document lacks '{}' property. Setting defaults...".format(shadow_property))
        change_shadow_value(SHADOW_VALUE_DEFAULT)
        return

    except Exception as e:
        exit("Error getting shadow: {}".format(repr(e)))

def on_get_rejected(error):
    # type: (iotshadow.ErrorResponse) -> None
    if error.code == 404:
        print("Thing has no shadow document. Creating with defaults...")
        change_shadow_value(SHADOW_VALUE_DEFAULT)
    else:
        exit("Get request was rejected. code:{} message:'{}'".format(
            error.code, error.message))

def on_delta_event(delta):
    # type: (iotshadow.ShadowDeltaEvent) -> None
    try:
        print("Received shadow delta event.")
        if delta.state and (shadow_property in delta.state):
            value = delta.state[shadow_property]
            if value is None:
                print("  Delta reports that '{}' was deleted. Resetting defaults...".format(shadow_property))
                change_shadow_value(SHADOW_VALUE_DEFAULT)
                return
            else:
                print("  Delta reports that desired value is '{}'. Changing local value...".format(value))
                change_shadow_value(value)
        else:
            print("  Delta did not report a change in '{}'".format(shadow_property))

    except Exception as e:
        exit("Error processing shadow delta: {}".format(repr(e)))

def on_publish_update(future):
    #type: (futures.Future) -> None
    try:
        future.result()
        print("Update request published.")
    except Exception as e:
        exit("Failed to publish update request: {}".format(repr(e)))

def on_update_accepted(response):
    # type: (iotshadow.UpdateShadowResponse) -> None
    print("Finished updating reported shadow value to '{}'.".format(response.state.reported[shadow_property]))
    print("Enter desired value: ") # remind user they can input new values

def on_update_rejected(error):
    # type: (iotshadow.ErrorResponse) -> None
    exit("Update request was rejected. code:{} message:'{}'".format(
        error.code, error.message))

def set_local_value_due_to_initial_query(reported_value):
    with shadow_value_lock:
        global shadow_value
        shadow_value = reported_value
    print("Enter desired value: ") # remind user they can input new values

def change_shadow_value(value):
    global shadow_value
    with shadow_value_lock:
        if shadow_value == value:
            print("Local value is already '{}'.".format(value))
            print("Enter desired value: ") # remind user they can input new values
            return

        print("Changed local shadow value to '{}'.".format(value))
        shadow_value = value

    print("Updating reported shadow value to '{}'...".format(value))
    try:
        request = iotshadow.UpdateShadowRequest(
            thing_name=thing_name,
            state=iotshadow.ShadowState(
                reported={ shadow_property: value },
                desired={ shadow_property: value },
            )
        )
        future = shadow_client.publish_update(request)
        future.add_done_callback(on_publish_update)
    except Exception as e:
        exit("Error issuing shadow update: {}".format(repr(e)))

def user_input_thread_fn():
    while True:
        try:
            # Read user input
            try:
                new_value = raw_input() # python 2 only
            except NameError:
                new_value = input() # python 3 only

            # If user wants to quit sample, then quit.
            # Otherwise change the shadow value.
            if new_value in ['exit', 'quit']:
                exit("User has quit")
                break
            else:
                change_shadow_value(new_value)

        except Exception as e:
            exit("Exception on input thread: {}".format(repr(e)))
            break

if __name__ == '__main__':
    # Process input args
    args = parser.parse_args()
    thing_name = args.thing_name
    shadow_property = args.shadow_property

    # Spin up resources
    event_loop_group = io.EventLoopGroup(1)
    client_bootstrap = io.ClientBootstrap(event_loop_group)

    tls_options = io.TlsContextOptions.create_client_with_mtls(args.cert, args.key)
    if args.root_ca:
        tls_options.override_default_trust_store(ca_path=None, ca_file=args.root_ca)
    tls_context = io.ClientTlsContext(tls_options)

    mqtt_client = mqtt.Client(client_bootstrap, tls_context)

    port = 443 if io.is_alpn_available() else 8883
    print("Connecting to {} on port {}...".format(args.endpoint, port))
    mqtt_connection = mqtt.Connection(
            client=mqtt_client,
            client_id=args.client_id)
    mqtt_connection.connect(
            host_name = args.endpoint,
            port = port,
            on_connect=on_connected,
            on_disconnect=on_disconnected,
            use_websocket=False,
            alpn=None,
            clean_session=True,
            keep_alive=6000)

    shadow_client = iotshadow.IotShadowClient(mqtt_connection)

    # Wait for connection to be fully established.
    # Note that it's not necessary to wait, commands issued to the
    # mqtt_connection before its fully connected will simply be queued.
    # But this sample waits here so it's obvious when a connection
    # fails or succeeds.
    connected_future.result()

    try:
        # Subscribe to necessary topics.
        # Note that is **is** important to wait for "accepted/rejected" subscriptions
        # to succeed before publishing the corresponding "request".
        print("Subscribing to Delta events...")
        delta_subscribed_future = shadow_client.subscribe_to_delta_events(
            request=iotshadow.ShadowDeltaEventsSubscriptionRequest(args.thing_name),
            on_delta=on_delta_event)

        # Wait for subscription to succeed
        delta_subscribed_future.result()

        print("Subscribing to Update responses...")
        update_accepted_subscribed_future = shadow_client.subscribe_to_update_accepted(
            request=iotshadow.UpdateShadowSubscriptionRequest(args.thing_name),
            on_accepted=on_update_accepted)

        update_rejected_subscribed_future = shadow_client.subscribe_to_update_rejected(
            request=iotshadow.UpdateShadowSubscriptionRequest(args.thing_name),
            on_rejected=on_update_rejected)

        # Wait for subscriptions to succeed
        update_accepted_subscribed_future.result()
        update_rejected_subscribed_future.result()

        print("Subscribing to Get responses...")
        get_accepted_subscribed_future = shadow_client.subscribe_to_get_accepted(
            request=iotshadow.GetShadowSubscriptionRequest(args.thing_name),
            on_accepted=on_get_accepted)

        get_rejected_subscribed_future = shadow_client.subscribe_to_get_rejected(
            request=iotshadow.GetShadowSubscriptionRequest(args.thing_name),
            on_rejected=on_get_rejected)

        # Wait for subscriptions to succeed
        get_accepted_subscribed_future.result()
        get_rejected_subscribed_future.result()

        # The rest of the sample runs asyncronously.

        # Issue request for shadow's current state.
        # The response will be received by the on_get_accepted() callback
        print("Requesting current shadow state...")
        publish_get_future = shadow_client.publish_get(
            request=iotshadow.GetShadowRequest(args.thing_name))

        # Ensure that publish succeeds
        publish_get_future.result()

        # Launch thread to handle user input.
        # A "daemon" thread won't prevent the program from shutting down.
        print("Launching thread to read user input...")
        user_input_thread = threading.Thread(target=user_input_thread_fn, name='user_input_thread')
        user_input_thread.daemon = True
        user_input_thread.start()

    except Exception as e:
        exit(repr(e))

    # Wait for the sample to finish (user types 'quit', or an error occurs)
    is_sample_done.wait()