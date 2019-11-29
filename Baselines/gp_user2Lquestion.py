
import pandas
import data
import data_utils
import pandas as pd
import gp_features
from datetime import timedelta

start_time =  data_utils.make_datetime("01.01.2014 00:01")
hour_threshold_suggested_answer = 5

cached_data = data.DataHandleCached()
data_handle = data.Data()

def is_user_answers_suggested_event(event):
    return event.question_age_at_answer <= timedelta(hours=hour_threshold_suggested_answer)

def get_suggestable_questions(time):
    open_questions = cached_data.open_questions_at_time(time)
    mask = (open_questions.question_date >= time - timedelta(hours=hour_threshold_suggested_answer))
    return open_questions[mask]




what_algo_observed = pd.DataFrame()

all_features_collection = gp_features.GP_Feature_Collection(gp_features.GP_Features_affinity(), gp_features.GP_Features_Question(), gp_features.GP_Features_user())
    # gp_features.GP_Features_TTM())

# out_feats = [] # for testing purposes
for i, event in enumerate(data_utils.all_answer_events_iterator(data_handle, start_time=start_time)):

    if not is_user_answers_suggested_event(event):
        #Don't just update the coupe, also add to the df as observation
        all_features_collection.update_event(event)
    else:
        target_user_id = event.answerer_user_id
        actually_answered_id = event.question_id
        event_time = event.answer_date

        suggestable_questions = get_suggestable_questions(event.answer_date)

        label = (suggestable_questions.question_id == actually_answered_id)
        # compute features
        features = all_features_collection.compute_features(target_user_id, suggestable_questions, event_time)
        # previous version: (I changed it because it is not necessary to give a list of target_user_id)
        # features = all_features_collection.compute_features(len(suggestable_questions)*[target_user_id], suggestable_questions, event_time)
        
        # out_feats.append(features) # for testing purposes

        # update features with new event (ONLY IF it is pos)
        all_features_collection.update_event(event)

        # use gp to predict
        # select candidates

        #
        


    if i > 300:
        ## for test purposes
        # concat = pd.concat(out_feats)
        # concat.to_csv("test.csv") 
        break
