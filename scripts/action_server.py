#!/usr/bin/env python

import rospy
from gazebo_msgs.msg import ModelState,ModelStates
from std_msgs.msg import String
from cse571_project.srv import *
import numpy as np
import tf
import math
import copy
import json
from collections import namedtuple

class RobotActionsServer:

    def __init__(self, object_dict, root_path, random_seed=10):
        self.object_dict = object_dict
        self.grid_size = object_dict['grid_size']
        self.failure = -1
        self.success = 1
        self.status = String(data='Idle')
        self.model_state_publisher = rospy.Publisher("/gazebo/set_model_state",ModelState,queue_size = 10)
        self.action_publisher = rospy.Publisher("/actions", String, queue_size=10)
        self.status_publisher = rospy.Publisher("/status", String, queue_size=10)
        self.random_seed = random_seed
        self.current_state = self.generate_init_state()
        self.action_config = self.load_action_config(root_path + '/action_config.json')
        self.direction_list = ["NORTH","EAST","SOUTH","WEST"]
        np.random.seed(self.random_seed)
        rospy.Service("execute_action", ActionMsg,self.execute_action)
        rospy.Service('get_all_actions', GetActions, self.get_all_actions)
        rospy.Service('get_possible_actions', GetPossibleActions, self.get_possible_actions)
        rospy.Service('get_possible_states', GetPossibleStates, self.get_possible_states)
        rospy.Service('get_reward', GetReward, self.get_reward)
        rospy.Service('is_terminal_state', IsTerminalState, self.is_terminal_state_handler)
        rospy.Service('get_current_state', GetInitialState, self.get_current_state)
        print "Action Server Initiated"


    def generate_init_state(self):
        state = {}
        state['robot'] = {'x': 0.0, 'y': 0.0, 'orientation': 'EAST'}
        for dirt_id in self.object_dict["dirts"]:
            state[dirt_id] = {
                            'x': float(self.object_dict["dirts"][dirt_id]["loc"][0]),
                            'y': float(self.object_dict["dirts"][dirt_id]["loc"][1]),
                        }
        return state


    def get_current_state(self, req):
        """
        This function will return initial state of turtlebot3.
        """
        return json.dumps(self.current_state)


    def load_action_config(self, action_config_file):
        f = open(action_config_file)
        action_config = json.load(f)
        f.close()
        return action_config


    def get_turtlebot_location(self,state):
        return state['robot']['x'], state['robot']['y'], state['robot']['orientation']


    def change_gazebo_state(self, dirt_id, target_transform):
        model_state_msg = ModelState()
        model_state_msg.model_name = dirt_id
        model_state_msg.pose.position.x = target_transform[0]
        model_state_msg.pose.position.y = target_transform[1]
        model_state_msg.pose.position.z = target_transform[2]
        self.model_state_publisher.publish(model_state_msg)

    def is_terminal_state_handler(self, req):
        state = json.loads(req.state)
        return self.is_terminal_state(state)


    def is_terminal_state(self, state):
        # Terminal state is reached when all dirts are cleaned
        cnt = 0
        for key in state.keys():
            if key.startswith('dirt'):
                if 'cleaned' in state[key]:
                    cnt += 1

        if cnt == len(self.object_dict["dirts"].keys()):
            return 1
        else:
            return 0


    def get_all_actions(self, req):
        return ','.join(self.action_config.keys())


    def get_possible_actions(self, req):
        state = req.state
        
        # These actions are executable anywhere in the environment
        action_list = ['clean', 'TurnCW', 'TurnCCW']

        # Check if we can execute moveF
        success, next_state = self.execute_moveF(state)
        if success == 1:
            action_list.append('moveF')
        return ','.join(action_list)


    def get_possible_states(self, req):
        state = json.loads(req.state)
        action = req.action
        action_params = json.loads(req.action_params)

        next_states = {}
        i = 1
        for possible_action in self.action_config[action]['possibilities']:
            
            state_key = 'state_{}'.format(i)
            i += 1

            if possible_action == "noaction":
                next_states[state_key] = (state, self.action_config[action]['possibilities'][possible_action])
                continue
            
            # generate calling function
            calling_params = []
            for param in self.action_config[possible_action]['params']:
                calling_params.append("'" + action_params[param] + "'")
            calling_params.append("'" + json.dumps(state) + "'")
            calling_function = "self.{}({})".format(self.action_config[possible_action]['function'], ','.join(calling_params))
            success, next_state = eval(calling_function)

            next_states[state_key] = (next_state, self.action_config[action]['possibilities'][possible_action])

        return json.dumps(next_states)


    def get_reward(self, req):
        state = json.loads(req.state)
        action = req.action
        next_state = json.loads(req.next_state)

        if state == next_state:
            return self.action_config[action]['fail_reward']
        else:
            return self.action_config[action]['success_reward']
    

    def execute_action(self, req):
        action = req.action_name
        params = json.loads(req.action_params)

        # Choose an action based on probabilities in action config
        chosen_action = np.random.choice(self.action_config[action]['possibilities'].keys(),
                                         p=self.action_config[action]['possibilities'].values())

        if chosen_action == "noaction":
            return self.failure, json.dumps(self.current_state)

        # generate calling function
        calling_params = []
        for param in self.action_config[chosen_action]['params']:
            calling_params.append("'" + params[param] + "'")
        calling_params.append("'" + json.dumps(self.current_state) + "'")
        calling_params.append('True')
        calling_function = "self.{}({})".format(self.action_config[chosen_action]['function'], ','.join(calling_params))
        success, next_state = eval(calling_function)
        
        # Update state
        self.current_state = copy.deepcopy(next_state)
        return success, json.dumps(next_state)

    def execute_clean(self, dirt_id, current_state, simulation=False):
        current_state = json.loads(current_state)
        robot_state = self.get_turtlebot_location(current_state)
        next_state = copy.deepcopy(current_state)

        # Valid dirt and dirt isn't already cleaned
        if dirt_id in self.object_dict["dirts"]:
            # Robot is at the location of the dirt
            dLoc=self.object_dict["dirts"][dirt_id]["loc"]
            if robot_state[0]==dLoc[0] and robot_state[1]==dLoc[1]:
                # Update gazebo environment if needed
                if simulation:
                    self.change_gazebo_state(dirt_id, list([-5, -5, 0.5]))
                    rospy.Rate(1).sleep()

                # Clear the blocked edge in the environment
                self.status_publisher.publish(self.status)

                # Update state
                next_state[dirt_id]['x'] = -5
                next_state[dirt_id]['y'] = -5
                # next_state[dirt_id]['cleaned'] = True

                return self.success, next_state

        self.status_publisher.publish(self.status)
        return self.failure, next_state

    def execute_moveF(self, current_state, simulation=False):
        current_state = json.loads(current_state)
        robot_state = self.get_turtlebot_location(current_state)
        next_state = copy.deepcopy(current_state)
        x1 = robot_state[0]
        y1 = robot_state[1]

        # Get new location
        if "EAST" in robot_state[2]:
            x2 = x1 + 1
            y2 = y1
        elif "WEST" in robot_state[2]:
            x2 = x1 - 1
            y2 = y1
        elif "NORTH" in robot_state[2]:
            x2 = x1
            y2 = y1 + 1
        else:
            x2 = x1
            y2 = y1 - 1

        if x2<0 or y2<0 or x2>self.grid_size or y2>self.grid_size:
            return self.success, current_state

        action_str = "MoveF"

        # Make bot move if simulating in gazebo
        if simulation:
            self.action_publisher.publish(String(data=action_str))
            rospy.wait_for_message("/status", String)

        # Update State
        next_state['robot']['x'] = x2
        next_state['robot']['y'] = y2

        return self.success, next_state


    def execute_TurnCW(self, current_state, simulation=False):
        current_state = json.loads(current_state)
        next_state = copy.deepcopy(current_state)

        # Make bot move if simulating in gazebo
        if simulation:
            action_str = "TurnCW"
            self.action_publisher.publish(String(data=action_str))
            rospy.wait_for_message("/status",String)

        # Update state
        current_orientation = current_state['robot']['orientation']
        new_orientation = self.direction_list[(self.direction_list.index(current_orientation) + 1)%4]
        next_state['robot']['orientation'] = new_orientation

        return self.success, next_state


    def execute_TurnCCW(self, current_state, simulation=False):
        current_state = json.loads(current_state)
        next_state = copy.deepcopy(current_state)
        
        # Make bot move if simulating in gazebo
        if simulation:
            action_str = "TurnCCW"
            self.action_publisher.publish(String(data=action_str))
            rospy.wait_for_message("/status",String)
        
        # Update state
        current_orientation = current_state['robot']['orientation']
        new_orientation = self.direction_list[(self.direction_list.index(current_orientation) - 1)%4]
        next_state['robot']['orientation'] = new_orientation

        return self.success, next_state


if __name__ == "__main__":
    object_dict = None
    RobotActionsServer(object_dict)
